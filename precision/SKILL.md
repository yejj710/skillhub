---
name: vllm-ascend-fix
description: 修复vLLM/vLLM-Ascend推理精度问题
---
## 概述
本skill用于诊断和修复vLLM及vLLM-Ascend框架下大模型推理精度异常问题。
## 工作流程
### 步骤1：拉起服务
#### 服务启动配置
- **启动脚本位置**: `/workspace/sh/test_sh/signel_vllm.sh`
- **启动命令**: 进入脚本路径，执行`nohup bash signel_vllm.sh > log.log 2>&1 &`
- **日志位置**: 当前路径下的`log.log`
- **成功标志（日志中包含）**: 
`(xxx) INFO:     Started server process [458]
(xxx) INFO:     Waiting for application startup.
(xxx) INFO:     Application startup complete.`
#### 执行步骤
1. 进入启动脚本目录
2. 执行启动命令
3. 监控日志确认服务运行正常
4. 如果启动失败：
   - 分析错误日志
   - 定位根本原因
   - 修复问题
   - 重新从步骤1开始
#### 命令示例
```bash
# 进入脚本目录
cd /workspace/sh/test_sh/
# 启动服务
注意，启动服务命令每轮只能执行一次！！！
nohup bash signel_vllm.sh > log.log 2>&1 &
# 监控日志
tail -f log.log
```
注意：多个命令不能通过一条执行完成，如cd /workspace/sh/test_sh/ 与 nohup bash signel_vllm.sh > log.log 2>&1 & ，必须一次执行，不能cd /workspace/sh/test_sh/  &&  nohup bash signel_vllm.sh > log.log 2>&1 & 执行，防止bash tool 超时
#### 成功判断标准
当日志中包含 
`(xxx) INFO:     Started server process [458]
(xxx) INFO:     Waiting for application startup.
(xxx) INFO:     Application startup complete.`
 时，视为服务启动成功。
注意：启动过程会比较慢，可能需要十多分钟，需要等待。等待过程只能查看日志。启动过程中，建议从5分钟开始，每分钟查询一次日志，避免因多次查看日志，导致会话上下文过长。
---
### 步骤2：执行测试用例
#### 测试配置
- **测试脚本位置**: `/workspace/sh/test_sh/curl.py`
- **执行命令**: `python curl.py > curl_test.log`
- **输出日志位置**: 当前路径下`curl_test.log`
注意：需要阅读curl.py 与signel_vllm.sh 中的ip、端口、模型名称是否匹配，如果不对需要提示出来。
#### 执行步骤
1. 运行测试脚本发送推理请求
2. 捕获并查看输出日志
3. 进入步骤3进行结果验证
#### 命令示例
```bash
# 进入测试脚本目录
cd /workspace/sh/test_sh/
# 执行测试用例
python curl.py > curl_test.log
# 查看结果
cat curl_test.log
```
---
### 步骤3：验证结果并修复问题
#### 基础信息
vllm路径： /vllm-workspace/vllm-deepseekv4
vllm-ascend路径：/vllm-workspace/vllm-ascend-deepseekv4
模型权重路径： /tmp/117/DeepSeek-V4-Flash-w8a8-mtp/DeepSeek-V4-Flash-w8a8-mtp
同时可分析 启动脚本 signel_vllm.sh，查看当前的服务配置，用于问题分析。
#### 预期结果
- **正确输出**: `B`
- **验证方法**: 阅读步骤2的日志与预期正确结果对比
#### 验证步骤
1. 读取推理输出日志
2. 与预期的正确结果对比
3. 如果结果正确：
   - 任务完成
   - 无需进一步操作
#### 如果结果不正确
1. **分析问题**:
   - 检查推理日志中的异常信息
   - 定位精度相关的错误
   - 追踪问题代码路径
2. **修复策略**:
   - **重要**: 只修改 `vllm-ascend` 代码
   - **禁止修改**: `vllm` 代码
   - vllm-ascend常见修复位置：
     - 算子实现
     - 数据类型转换
     - 数值精度处理
     - Attention机制实现
3. **应用修复**:
   - 在vllm-ascend中进行必要的代码修改
   - 记录所做的修改
   - 保存修复方案供后续参考
4. **重新测试**:
   - 返回**步骤1**重新执行整个流程
   - 持续迭代直到精度问题解决
#### 常见精度问题检查清单
- [ ] 浮点数精度问题（FP32/FP16/BF16）
- [ ] Attention计算中的数值稳定性
- [ ] 数据类型转换精度
- [ ] 算子实现正确性
- [ ] 内存对齐问题
- [ ] 算子融合导致的精度损失
---
## 调试技巧
### 日志分析
- 错误模式: `[ERROR] xxx`
### vllm-ascend常见问题区域
1. NPU自定义算子
2. Attention内核实现
3. 数据转换工具
4. 量化/反量化逻辑
### 常用命令
```bash
# 检查vllm-ascend安装
pip show vllm-ascend
# 查看vllm-ascend源码位置
pip show vllm-ascend | grep Location
# 监控进程使用情况
ps -ef
# npu使用查看
npu-smi info
# 停止进程
ps -ef | grep "python" | grep -v grep | awk '{print $2}' | xargs -t -i kill -9 {}
ps -ef | grep "VLLM" | grep -v grep | awk '{print $2}' | xargs -t -i kill -9 {}
```
---
## 注意事项
- 运行测试前务必确认服务已完全启动
- 记录所有代码变更以便回滚
- 文档化每次修复尝试及其结果
- 如需多次迭代，考虑缓存中间结果
- 每次修改需要记录到本地fix_x.md中，x按修改的次数逐次累加，如1,2,3扥等。
- 要求：每次执行的shell命令必须显示出来
- 注意：多个命令不能通过一条执行完成，如cd xxx 与 bash xx，必须一次执行，不能cd xxx && bash xx 执行。
