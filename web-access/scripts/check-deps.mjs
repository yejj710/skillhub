#!/usr/bin/env node
// 环境检查 + 确保 CDP Proxy 就绪（跨平台，替代 check-deps.sh）

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PROXY_SCRIPT = path.join(ROOT, 'scripts', 'cdp-proxy.mjs');
const PROXY_PORT = Number(process.env.CDP_PROXY_PORT || 3456);

// --- Node.js 版本检查 ---

function checkNode() {
  const major = Number(process.versions.node.split('.')[0]);
  const version = `v${process.versions.node}`;
  if (major >= 22) {
    console.log(`node: ok (${version})`);
  } else {
    console.log(`node: warn (${version}, 建议升级到 22+)`);
  }
}

// --- TCP 端口探测 ---

function checkPort(port, host = '127.0.0.1', timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = net.createConnection(port, host);
    const timer = setTimeout(() => { socket.destroy(); resolve(false); }, timeoutMs);
    socket.once('connect', () => { clearTimeout(timer); socket.destroy(); resolve(true); });
    socket.once('error', () => { clearTimeout(timer); resolve(false); });
  });
}

// --- Chrome 调试端口检测（DevToolsActivePort 多路径 + 常见端口回退） ---

function activePortFiles() {
  const home = os.homedir();
  const localAppData = process.env.LOCALAPPDATA || '';
  switch (os.platform()) {
    case 'darwin':
      return [
        path.join(home, 'Library/Application Support/Google/Chrome/DevToolsActivePort'),
        path.join(home, 'Library/Application Support/Google/Chrome Canary/DevToolsActivePort'),
        path.join(home, 'Library/Application Support/Chromium/DevToolsActivePort'),
      ];
    case 'linux':
      return [
        path.join(home, '.config/google-chrome/DevToolsActivePort'),
        path.join(home, '.config/chromium/DevToolsActivePort'),
      ];
    case 'win32':
      return [
        path.join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort'),
        path.join(localAppData, 'Chromium/User Data/DevToolsActivePort'),
      ];
    default:
      return [];
  }
}

async function detectChromePort() {
  const inspectedFiles = [];

  // 优先从 DevToolsActivePort 文件读取
  for (const filePath of activePortFiles()) {
    try {
      const lines = fs.readFileSync(filePath, 'utf8').trim().split(/\r?\n/).filter(Boolean);
      const port = parseInt(lines[0], 10);
      if (!(port > 0 && port < 65536)) {
        inspectedFiles.push({ filePath, exists: true, port: null, reachable: false, invalid: true });
        continue;
      }
      const reachable = await checkPort(port);
      inspectedFiles.push({ filePath, exists: true, port, reachable, invalid: false });
      if (reachable) {
        return { port, source: 'DevToolsActivePort', filePath, inspectedFiles };
      }
    } catch (_) {}
  }

  // 回退：探测常见端口
  for (const port of [9222, 9229, 9333]) {
    if (await checkPort(port)) {
      return { port, source: 'common-port-scan', filePath: null, inspectedFiles };
    }
  }

  return { port: null, source: null, filePath: null, inspectedFiles };
}

function printChromeFailure(details) {
  const visibleButUnreachable = details.inspectedFiles.filter((entry) => entry.exists && entry.port && !entry.reachable);
  const invalidFiles = details.inspectedFiles.filter((entry) => entry.invalid);

  if (visibleButUnreachable.length) {
    console.log('chrome: host session detected, but unreachable from current runtime');
    for (const entry of visibleButUnreachable) {
      console.log(`  DevToolsActivePort: ${entry.filePath}`);
      console.log(`  发现端口 ${entry.port}，但当前会话无法连接 127.0.0.1:${entry.port}`);
    }
    console.log('  这通常不是 Chrome 没开，而是当前沙箱/代理会话看不到宿主机浏览器的 localhost。');
    console.log('  如果你在自己的 terminal 里运行这条命令是成功的，请优先改用提权环境执行该命令。');
    console.log('  若你是通过 Agent 调用：建议对 node/curl 检查步骤申请 require_escalated 后重试。');
    console.log("  若你是手动排查：可在宿主机终端执行 curl --noproxy '*' http://127.0.0.1:<端口>/json/version 验证。");
    return;
  }

  if (invalidFiles.length) {
    console.log('chrome: found DevToolsActivePort file, but content is invalid');
    for (const entry of invalidFiles) {
      console.log(`  文件内容异常：${entry.filePath}`);
    }
    console.log('  请重启 Chrome 后重试；若在 Agent 中运行，必要时改用提权环境。');
    return;
  }

  console.log('chrome: not connected');
  console.log('  请确保 Chrome 已打开，然后访问 chrome://inspect/#remote-debugging 并勾选 Allow remote debugging');
  console.log('  如果你在自己的 terminal 中运行成功，而当前环境失败，这通常是沙箱/代理会话无法访问宿主机 Chrome。');
  console.log("  建议优先在提权环境中重试，或在宿主机终端用 curl --noproxy '*' 验证 127.0.0.1:9222 /json/version 是否可达。");
}

// --- CDP Proxy 启动与等待 ---

function httpGetJson(url, timeoutMs = 3000) {
  return fetch(url, { signal: AbortSignal.timeout(timeoutMs) })
    .then(async (res) => {
      try { return JSON.parse(await res.text()); } catch { return null; }
    })
    .catch(() => null);
}

function startProxyDetached() {
  const logFile = path.join(os.tmpdir(), 'cdp-proxy.log');
  const logFd = fs.openSync(logFile, 'a');
  const child = spawn(process.execPath, [PROXY_SCRIPT], {
    detached: true,
    stdio: ['ignore', logFd, logFd],
    ...(os.platform() === 'win32' ? { windowsHide: true } : {}),
  });
  child.unref();
  fs.closeSync(logFd);
}

async function ensureProxy() {
  const targetsUrl = `http://127.0.0.1:${PROXY_PORT}/targets`;

  // /targets 返回 JSON 数组即 ready
  const targets = await httpGetJson(targetsUrl);
  if (Array.isArray(targets)) {
    console.log('proxy: ready');
    return true;
  }

  // 未运行或未连接，启动并等待
  console.log('proxy: connecting...');
  startProxyDetached();

  // 等 proxy 进程就绪
  await new Promise((r) => setTimeout(r, 2000));

  for (let i = 1; i <= 15; i++) {
    const result = await httpGetJson(targetsUrl, 8000);
    if (Array.isArray(result)) {
      console.log('proxy: ready');
      return true;
    }
    if (i === 1) {
      console.log('⚠️  Chrome 可能有授权弹窗，请点击「允许」后等待连接...');
    }
    await new Promise((r) => setTimeout(r, 1000));
  }

  console.log('❌ 连接超时，请检查 Chrome 调试设置');
  console.log(`  日志：${path.join(os.tmpdir(), 'cdp-proxy.log')}`);
  return false;
}

// --- main ---

async function main() {
  checkNode();

  const chromeDetection = await detectChromePort();
  if (!chromeDetection.port) {
    printChromeFailure(chromeDetection);
    process.exit(1);
  }
  const sourceSuffix = chromeDetection.source === 'DevToolsActivePort'
    ? ` via DevToolsActivePort`
    : chromeDetection.source === 'common-port-scan'
      ? ' via port scan'
      : '';
  console.log(`chrome: ok (port ${chromeDetection.port}${sourceSuffix})`);

  const proxyOk = await ensureProxy();
  if (!proxyOk) {
    process.exit(1);
  }

  // 列出已有站点经验
  const patternsDir = path.join(ROOT, 'references', 'site-patterns');
  try {
    const sites = fs.readdirSync(patternsDir)
      .filter(f => f.endsWith('.md'))
      .map(f => f.replace(/\.md$/, ''));
    if (sites.length) {
      console.log(`\nsite-patterns: ${sites.join(', ')}`);
    }
  } catch {}

}

await main();
