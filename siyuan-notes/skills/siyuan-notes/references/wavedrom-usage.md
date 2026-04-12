# WaveDrom 用法参考

## 概述

WaveDrom 是一个用于绘制数字时序图（Timing Diagram）的 JavaScript 库，使用 **JSON 格式**描述波形图。

**在思源笔记中使用场景**：
- ✅ **用于绘制时序图/波形图**（Timing Diagram）
- ❌ 绘制流程图、类图等 → 请使用 **Mermaid**

---

## 在思源笔记中使用 WaveDrom

语法格式：
````markdown
~~~wavedrom
{ signal: [
  { name: "clk", wave: "p......."}
]}
~~~
````

---

## 基础语法

### 最简示例

```json
{ signal: [{ name: "clk", wave: "p......."}]}
```

### 多信号波形

```json
{ signal: [
  { name: "clk", wave: "p......."},
  { name: "data", wave: "01.0..x."}
]}
```

---

## 波形字符说明

每个波形字符代表一个时间周期：

| 字符 | 含义 | 说明 |
|------|------|------|
| `0` | 低电平 | 数字 0 |
| `1` | 高电平 | 数字 1 |
| `x` | 未知/不定态 | X 状态 |
| `z` | 高阻态 | Z 状态 |
| `.` | 延续 | 延续前一个状态 |
| ` \| ` | 时间间隙 | 插入时间间隙 |
| `=` | 数据 | 使用 data 数组 |
| `2-9` | 其他状态 | 可自定义状态 |

---

## 时钟信号

时钟每周期变化两次，支持正负极性：

| 字符 | 类型 | 波形 |
|------|------|------|
| `p` | 正沿时钟 | _/~~/ |
| `P` | 带标记正沿时钟 | _/~~/ (带箭头) |
| `n` | 负沿时钟 | /~~\_ |
| `N` | 带标记负沿时钟 | /~~\_ (带箭头) |

### 时钟示例

~~~wavedrom
{ signal: [
  { name: "pclk", wave: "p......."},
  { name: "Pclk", wave: "P......."},
  { name: "nclk", wave: "n......."},
  { name: "Nclk", wave: "N......."}
]}
~~~

---

## 数据标签

使用 `data` 数组为波形添加标签：

```json
{ signal: [
  { name: "clk", wave: "P......"},
  { name: "data", wave: "x.345x.", data: ["head", "body", "tail"]}
]}
```

- `=` 字符显示对应的数据项
- 序号从 0 开始

### 数据示例

~~~wavedrom
{ signal: [
  { name: "clk", wave: "p.P.P.P." },
  { name: "addr", wave: "x.=.x.=x", data: ["0xA0", "0xB0"] },
  { name: "data", wave: "x.=.x.=x", data: ["0xDE", "0xAD"] }
]}
~~~

---

## 分组信号

使用数组对信号进行分组（支持嵌套）：

```json
{ signal: [
  { name: "clk", wave: "p..Pp..P"},
  ["Master",
    { name: "addr", wave: "x3.x4..x", data: "A1 A2"},
    { name: "wdata", wave: "x3.x....", data: "D1"},
  ],
  {},
  ["Slave",
    { name: "ack", wave: "x01x0.1x"},
    { name: "rdata", wave: "x.....4x", data: "Q2"},
  ]
]}
```

- 数组首个元素为分组名称
- `{}` 添加垂直间隔

### 分组示例

~~~wavedrom
{ signal: [
  ["控制",
    { name: "clk", wave: "p......"},
    { name: "valid", wave: "01....0"}
  ],
  {},
  ["数据",
    { name: "addr", wave: "x.=.x..", data: ["0x100"]},
    { name: "data", wave: "x.==x..", data: ["0xAB", "0xCD"]}
  ]
]}
~~~

---

## 周期和相位

### period - 时间周期倍率

```json
{ signal: [
  { name: "CK", wave: "P.......", period: 2},
  { name: "DATA", wave: "x.3x=x4x"}
]}
```

### phase - 相位偏移

```json
{ signal: [
  { name: "CK", wave: "P......."},
  { name: "DATA", wave: "x.3x=x4x", phase: 0.5}
]}
```

| 值 | 含义 |
|-----|------|
| 0 | 左对齐 |
| 0.5 | 居中（中间采样） |

### 周期与相位示例

~~~wavedrom
{ signal: [
  { name: "时钟", wave: "p.P.P.P." },
  { name: "数据", wave: "x.=.x.=x", data: ["0x55", "0xAA"] },
  { name: "采样点", wave: ".P.P.P.P", phase: 0.5 }
]}
~~~

---

## 配置属性

### hscale - 水平缩放

```json
{ signal: [...], config: { hscale: 2 } }
```

值越大，波形越宽。

### skin - 皮肤样式

```json
config: { skin: "default" }   // 正常宽度
config: { skin: "narrow" }   // 窄宽度
```

### head - 头部信息

```json
{
  signal: [...],
  head: {
    text: "时钟信号示例",
    tick: 0,      // 第0个周期标记
    every: 2      // 每2个周期标记一次
  }
}
```

### foot - 尾部信息

```json
{
  signal: [...],
  foot: {
    text: "Figure 1",
    tock: 9       // 第9个周期后标记
  }
}
```

---

## 完整示例集合

### UART 波形图

~~~wavedrom
{ signal: [
  { name: "TX", wave: "1.0.1.0.1.0.1.0.1.1" },
  { name: "数据", wave: "x0.1.0.1.0.1.0.1.0x", data: ["", "Start", "0x55", "", "", "", "", "", ""]}
],
  config: { hscale: 2, skin: "narrow"},
  head: {
    text: "UART 发送 - 0xAA"
  }
}
~~~

### SPI 时序图

~~~wavedrom
{ signal: [
  ["Master",
    { name: "SCK", wave: "p......"},
    { name: "CS_N", wave: "10....1"},
    { name: "MOSI", wave: "0.====.", data: ["CMD", "ADDR0", "ADDR1", "DATA"]}
  ],
  ["Slave",
    { name: "MISO", wave: "x.====x", data: ["", "DUMMY", "DUMMY", "RESP"]}
  ]
]}
~~~

### I2C 时序图

~~~wavedrom
{ signal: [
  { name: "SCL", wave: "pp..pp.p"},
  { name: "SDA", wave: "1.0.1...0"},
  { name: "动作", wave: "xSTARTxSTOPx", data: ["", "", "ACK", ""]}
],
  config: { hscale: 2 }
}
~~~

### 状态机波形

~~~wavedrom
{ signal: [
  { name: "State", wave: "x.2..3...4.5x", data: ["IDLE", "RUN", "WAIT", "DONE"]},
  { name: "Signal1", wave: "0.1.....0.10"}
]}
~~~

### 复杂数据总线

~~~wavedrom
{ signal: [
  { name: "clk", wave: "p.P.P.P.P.P.P.P."},
  { name: "valid", wave: "01.......0"},
  { name: "addr", wave: "x.=......x", data: ["0x1000"]},
  { name: "data", wave: "x.======x", data: ["0xDE", "0xAD", "0xBE", "0xEF", "0xC0", "0xFF"]}
]}
~~~

### 时钟域交叉

~~~wavedrom
{ signal: [
  ["时钟域A",
    { name: "clk_a", wave: "p.P.P.P."},
    { name: "data_a", wave: "x.3.4.5.x", data: ["A", "B", "C"]}
  ],
  [],
  ["时钟域B",
    { name: "clk_b", wave: "n.n.n.n."},
    { name: "data_b", wave: "x...3..4x", data: ["A'", "C'"]}
  ]
]}
~~~

---

## 高级技巧

### 使用相位偏移显示采样点

```json
{ signal: [
  { name: "clk", wave: "p.P.P.P.P."},
  { name: "signal", wave: "x.=.x.=x", data: ["0x55", "0xAA"]},
  { name: "采样", wave: ".P.P.P.P", phase: 0.5}
]}
```

### 多层分组嵌套

```json
{ signal: [
  ["系统",
    ["子模块1",
      { name: "sig1", wave: "0.1..0"},
      { name: "sig2", wave: "x...1"}
    ],
    [],
    ["子模块2",
      { name: "sig3", wave: "1.0..1"}
    ]
  ]
]}
~~~

### 时间间隙标记

```json
{ signal: [
  { name: "上升", wave: "01......|01....."},
  { name: "下降", wave: "10....|10.."}
]}
```

---

## 常见问题

### 数据不对齐
- 检查 `phase` 参数
- 数据项数量应与 `=` 字符数量匹配

### 波形显示不完整
- 使用 `hscale` 增加水平缩放
- 使用 `skin: "narrow"` 减小垂直间距

### 字符编码问题
- 中文字符使用 Utf-8 编码

---

## 参考资源

- [WaveDrom 官方教程](https://wavedrom.com/tutorial.html)
- [WaveDrom 在线编辑器](https://wavedrom.com/)
- [Digital Timing Diagram Guide](https://github.com/drom/wavedrom)

---

**最后更新：2024-01**

**重要**：在思源笔记中，使用 `~~~wavedrom` 代码块，并确保 JSON 格式正确！