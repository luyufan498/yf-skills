---
name: jira-issue-submitter
description: ASIC项目JIRA问题单提交流程助手。当用户需要为ASIC PDT项目提交CBUG问题时使用：(1) 根据自然语言描述分析问题并推荐表单字段值，(2) 提供字段映射建议供用户确认，(3) 使用agent-browser自动填写并提交问题单。
---

# JIRA Issue Submitter

## Overview

此技能帮助用户为ASIC PDT项目快速提交CBUG问题单。通过分析用户的自然语言问题描述，自动化映射到JIRA表单的各个分类字段，减少手动填写的工作量。

## Core Workflow

```
用户提供问题描述 → 分析并映射字段 → 生成推荐清单 → 用户确认 → 使用agent-browser提交
```

## 第一步：问题分析与字段映射

### 分析用户提供的问题描述

从用户的自然语言描述中提取关键信息：

1. **环境信息**: UT、BT、IT、ST、Review、后仿等
2. **阶段信息**: TR3、85NL、95NL、100NL、TO等
3. **产品/模块**: Orion、Draco、Leonis、RDMA、寄存器、Memory等
4. **功能相关**: 核心、错误处理、缓存、时序、性能等
5. **影响程度**: 致命、严重、一般、提示等
6. **版本信息**: RTL版本、Testbench版本等

### 应用映射规则

使用 [field-mapping.md](references/field-mapping.md) 中的规则为每个字段生成推荐值：

- 使用关键词匹配（精确匹配 → 模糊匹配 → 上下文推断）
- 为每个推荐值标注置信度（高/中/低）
- 处理冲突情况，使用优先级规则

### 生成推荐清单

按以下格式向用户展示推荐清单：

```
## 问题分析结果

根据您的问题描述，我为您生成以下字段推荐：

### 必填字段

| 字段 | 推荐值 | 置信度 | 说明 |
|------|--------|--------|------|
| 验证环境 | IT | 高 | 明确标注环境 |
| 问题发现阶段 | 95NL~100NL | 高 | 场景说明的阶段 |
| 所属产品 | Orion-ASIC V3R1 | 中 | 需要确认 |
| ... | ... | ... | ... |

### 需要补充的字段

- RTL版本: 待输入
- Testbench版本: 待输入
- 测试版本: 待确认

### 概要建议

"问题: 简洁描述问题核心"
```

## 第二步：用户确认

等待用户确认推荐值。用户可以：

- 修改任何推荐的字段值
- 补充缺失的信息（如版本号、产品归属等）
- 重新调整严重程度或其他主观判断

确保所有必填字段都有值后再继续。

## 第三步：使用agent-browser提交

当用户确认字段值后，使用 agent-browser skill 执行以下步骤：

### Step 1: 登录与导航

1. 打开 http://jira.dpu.tech/secure/CreateIssue!default.jspa
2. `agent-browser snapshot -i` 检查当前页面状态
3. 判断是否需要登录：
   - 如果页面包含"登录"链接但没有登录表单，说明需要登录
   - 如果页面已经是表单页面，跳过登录步骤
4. 如果需要登录：
   - 尝试使用保存的认证配置：`agent-browser auth login jira`（如果存在）
   - 或者使用环境变量登录：
     * 从环境变量读取：`$NBL_USERNAME` 和 `$NBL_PASSWORD`
     * 导航到登录页面：`agent-browser open http://jira.dpu.tech/login.jsp`
     * 等待加载：`agent-browser wait --load networkidle`
     * 获取表单元素：`agent-browser snapshot -i`
     * 填写用户名：`agent-browser fill @eN "$NBL_USERNAME"`（@eN 为用户名输入框）
     * 填写密码：`agent-browser fill @eM "$NBL_PASSWORD"`（@eM 为密码输入框）
     * 点击登录：`agent-browser click @eLogin`（@eLogin 为登录按钮）
     * 等待登录完成：`agent-browser wait --load networkidle`
     * 验证登录：检查 URL 是否跳转到主页或创建页面
   - 如果环境变量不存在或登录失败，提示用户提供凭证或手动登录
5. 登录成功后导航到创建页面：`agent-browser open http://jira.dpu.tech/secure/CreateIssue!default.jspa`
6. 验证当前页面：项目为"ASIC PDT (ASIC)"，问题类型为"CBUG"
7. 点击"下一步"按钮进入填写表单页面（使用 `agent-browser snapshot -i` 获取按钮元素，然后 `agent-browser click @e<Next>` 点击）

**使用提示**:
- 参考 agent-browser 文档中的登录和导航命令
- 使用 `snapshot -i` 获取页面元素引用，元素引用在页面变化后会失效
- 可以使用 `get url` 检查当前页面是否已成功登录
- 建议使用 `agent-browser state save jira-auth.json` 保存登录状态供后续使用

### Step 2: 填写表单字段

根据用户确认的字段值，逐个填写表单：

**下拉选项字段**: 使用 `select` 命令选择值
- 验证环境
- 问题发现阶段
- 所属产品
- 所属子系统-模块
- 经办人（可使用"分配给我"按钮）
- 验证bug类型
- 严重程度
- 优先级
- 问题引入功能
- 测试版本

**文本框字段**: 使用 `fill` 命令填写值
- 概要
- 测试用例编号
- RTL版本
- Testbench版本

**使用提示**:
- 每次页面变化后使用 `snapshot -i` 获取新的元素引用
- 可以使用 `get value @eXX` 验证字段的值
- 参考 agent-browser 文档中的 `select` 和 `fill` 命令用法

### Step 3: 用户确认

填写完成后，执行以下操作：

1. 截图保存填写内容供用户确认
2. 等待用户确认无误

**使用提示**: 使用 `screenshot` 或 `screenshot --full` 命令

### Step 4: 提交

1. 点击提交按钮
2. 等待提交完成
3. 验证问题创建成功

**使用提示**:
- 提交前再次确认必填字段都已填写
- 提交后使用 `snapshot -i` 验证页面状态
- 截图保存提交结果

## 关键注意事项

1. **元素引用动态性**: agent-browser 的元素引用（@eXX）在页面变化后会改变，每次导航后需要重新获取
2. **错误处理**: 如果某个操作失败，等待并重试，必要时重新登录
3. **等待时间**: 页面加载需要时间，适当使用 `sleep` 等待页面就绪
4. **验证步骤**: 关键操作后验证状态，确保操作成功

## Resources

### references/field-mapping.md

详细的字段映射规则和关键词匹配表。在分析用户问题时，应该参考此文件来确定各个字段的推荐值。

### references/field-options.md

所有下拉选项的完整值列表，用于验证推荐值的有效性。

---

**重要提示**: 此技能聚焦于问题分析和字段映射流程，具体的 agent-browser 操作细节应由 agent 根据其能力文档自适应处理。不要在 SKILL.md 中硬编码所有的元素引用或命令序列。