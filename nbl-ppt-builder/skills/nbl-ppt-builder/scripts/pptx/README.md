# NBL PPT Builder - PPTX 生成工具

## 概述

将 HTML 演示文稿转换为 PowerPoint (.pptx) 文件，支持：
- ✅ 准确保留元素位置和尺寸
- ✅ 转换文本、图片、形状、列表
- ✅ 支持占位符（用于图表插入）
- ✅ 自动验证维度和溢出

## 工作原理

1. 收集所有 HTML 页面（按页码命名：`01_*.html`, `02_*.html`, ...）
2. 创建 PptxGenJS 演示文稿（16:9 布局）
3. 使用 Playwright 在浏览器中渲染每个 HTML 页面
4. 提取元素位置和样式，转换为 PPTX 对象
5. 保存最终 .pptx 文件

## 安装依赖

```bash
cd .claude/skills/nbl-ppt--builder/scripts/pptx
npm install
```

## 使用方法

### 基本用法

```bash
cd .claude/skills/nbl-ppt--builder/scripts/pptx
node generate_pptx.js /path/to/ppt_季度总结_20240131
```

### 指定输出文件名

```bash
node generate_pptx.js /path/to/ppt_季度总结_20240131 quarterly_report.pptx
```

## 输入要求

### 文件命名格式

HTML 文件必须按以下格式命名：

```
{页码:02d}_{描述}.html
```

### 示例文件列表

```
ppt_季度总结_20240131/
├── 01_home.html           # 封面页
├── 02_toc.html            # 目录页
├── 03_背景介绍.html
├── 04_问题分析.html
├── 05_关键要点.html
├── 06_实施流程.html
├── 07_数据分析.html
├── 08_成果展示.html
├── 09_未来规划.html
└── 10_end.html            # 结束页
```

## HTML 样式要求

### 必须遵守的样式规则

1. **所有文本必须在文本标签内**：
   - ✅ 正确: `<div><p>文本内容</p></div>`
   - ❌ 错误: `<div>文本内容</div>` - 文本不会出现在 PPT 中
   - ❌ 错误: `<span>文本</span>` - 文本不会出现在 PPT 中

2. **使用列表标签代替手动项目符号**：
   - ✅ 正确: `<ul><li>项目</li></ul>`
   - ❌ 错误: `<p>• 项目</p>`

3. **只使用 Web 安全字体**：
   - ✅ 允许: Arial, Helvetica, Times New Roman, Georgia, Courier New, Verdana, Tahoma, Trebuchet MS, Impact
   - ❌ 禁止: 自定义字体、Segoe UI、SF Pro、Roboto 等

4. **页面尺寸**：
   - 16:9 布局: `width: 720pt; height: 405pt;`

### 支持的 CSS 属性

| 属性 | 元素 | 转换结果 |
|------|------|---------|
| `color` | 文本 | ✓ |
| `background-color` | DIV | ✓ |
| `border` | DIV | ✓ |
| `border-radius` | DIV | ✓ |
| `box-shadow` | DIV | ✓ (仅外阴影) |
| `linear-gradient` | 任意 | ✗ 需先用 Sharp 转为 PNG |
| `text-align` | 文本 | ✓ |
| `font-weight` | 文本 | ✓ |
| `font-style` | 文本 | ✓ |
| `text-decoration` | 文本 | ✓ |

### 不支持的特性

- ❌ CSS 渐变 (`linear-gradient`, `radial-gradient`) - 需先转换为 PNG
- ❌ 自定义字体
- ❌ 文本元素上的背景、边框、阴影
- ❌ 内边距 (`inset` 阴影)

### 处理渐变和图标

**使用 Sharp 预渲染渐变为 PNG**：

```javascript
const sharp = require('sharp');

async function createGradient(filename) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="562.5">
    <defs>
      <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:#0B3BD3"/>
        <stop offset="100%" style="stop-color:#1D4FED"/>
      </linearGradient>
    </defs>
    <rect width="100%" height="100%" fill="url(#g)"/>
  </svg>`;

  await sharp(Buffer.from(svg)).png().toFile(filename);
  return filename;
}

// 在 HTML 中使用
// <body style="background-image: url('gradient.png');">
```

## 错误处理

### 常见错误

| 错误信息 | 原因 | 解决方法 |
|---------|------|---------|
| `HTML dimensions don't match` | Body 尺寸不匹配 16:9 | 检查 `width: 720pt; height: 405pt;` |
| `Content overflows body` | 内容超出幻灯片边界 | 减少内容或调整布局 |
| `Text not found` | 文本不在文本标签内 | 用 `<p>`、`<h1>`、`<ul>` 包裹文本 |
| `CSS gradients not supported` | 使用了 CSS 渐变 | 用 Sharp 预渲染为 PNG |

### 验证输出

脚本会显示详细的转换过程：

```
📊 NBL PPT Builder - Generating PowerPoint

📁 Found 10 slide(s):
   1. 01_home.html
   2. 02_toc.html
   3. 03_背景介绍.html
   ...

🔄 Converting slides to PowerPoint...
   📄 Converting 01_home.html... ✅
   📄 Converting 02_toc.html... ✅
   ...

💾 Saving to presentation.pptx...

✨ Done! 10 slides saved to presentation.pptx
```

## 示例

### 完整 HTML 模板示例

```html
<!DOCTYPE html>
<html>
<head>
<style>
body {
  width: 720pt;
  height: 405pt;
  margin: 0;
  padding: 0;
  background: #ffffff;
  font-family: Arial, sans-serif;
}
.content {
  margin: 30pt;
  padding: 40pt;
}
h1 {
  color: #0B3BD3;
  font-size: 32pt;
  font-weight: bold;
}
ul li {
  font-size: 18pt;
  color: #333333;
  margin-bottom: 12pt;
}
</style>
</head>
<body>
<div class="content">
  <h1>章节标题</h1>
  <ul>
    <li>第一要点</li>
    <li>第二要点</li>
    <li>第三要点</li>
  </ul>
</div>
</body>
</html>
```

## 注意事项

1. **图片路径**: 使用相对路径，如 `images/logo.png`
2. **颜色格式**: CSS 使用 `#rrggbb`，PptxGenJS 使用 `rrggbb` (无 # 前缀)
3. **单位**: 提倡使用 `pt` 单位（更精确的尺寸控制）
4. **验证**: 建议先用 `validate_with_playwright.py` 验证页面再转换

## 许可证

内部使用 - 与 NBL PPT Builder SKILL 一致