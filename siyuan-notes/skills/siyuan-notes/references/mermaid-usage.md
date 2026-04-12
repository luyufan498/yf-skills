# 思源笔记 Mermaid 用法参考

## 概述

Mermaid 是一个基于 JavaScript 的图表绘制工具，可以通过简单的文本语法生成流程图、时序图、甘特图等多种图表。思源笔记原生支持 Mermaid 渲染。

## 在思源笔记中使用 Mermaid

### 创建 Mermaid 图表

在思源笔记中，使用代码块输入 Mermaid 语法：

**Markdown语法三连字** 方式：
````markdown
```mermaid
graph TD
A[开始] --> B[处理]
B --> C[结束]
```
````

**官方文档推荐的三连字方式**（推荐）：
````markdown
~~~mermaid
graph TD
A[开始] --> B[处理]
B --> C[结束]
~~~
````

### 注意事项

- 使用 `~~~` 代替 ` ````
- 必须指定 `mermaid` 作为语言标识
- 图表会在编辑器中实时预览或点击预览时显示

## 支持的图表类型

### 1. 流程图 (Flowchart)

```mermaid
graph TD
A[开始] --> B{判断条件}
B --是--> C[执行操作]
B --否--> D[跳过]
C --> E[结束]
D --> E
```

**流程图方向：**
- `TD` 或 `TB` - 从上到下 (Top-Down/Top-Bottom)
- `BT` - 从下到上 (Bottom-Top)
- `LR` - 从左到右 (Left-Right)
- `RL` - 从右到左 (Right-Left)

**节点形状：**

| 形状 | 语法 | 说明 |
|------|------|------|
| 圆角矩形 | `A[文本]` | 标准流程节点 |
| 矩形 | `B[文本]` | 通用节点 |
| 菱形 | `C{文本}` | 判断/决策节点 |
| 圆形 | `D((文本))` | 连接节点 |
| 文档 | `E[文本/]` | 文档节点 |
| 数据库 | `F[(文本)]` | 数据库节点 |

**节点间连接：**
- `-->` - 实线箭头
- `---` - 实线
- `-.->` - 虚线箭头
- `===` - 粗线

**添加标签：**
```mermaid
graph LR
A -->|标签文本| B
```

### 2. 时序图 (Sequence Diagram)

```mermaid
sequenceDiagram
    participant A as 用户
    participant B as 系统
    participant C as 数据库

    A->>B: 发送请求
    B->>C: 查询数据
    C-->>B: 返回结果
    B-->>A: 响应结果
```

**时序图语法：**
- `->>` - 实线箭头（同步消息）
- `-->>` - 虚线箭头（异步消息/响应）
- `->` - 实线无箭头
- `-->` - 虚线无箭头
- `autonumber` - 自动编号（放在代码开头）

### 3. 类图 (Class Diagram)

```mermaid
classDiagram
    class Animal {
        +String name
        +int age
        +eat()
        +sleep()
    }
    class Dog {
        +bark()
    }
    class Cat {
        +meow()
    }
    Animal <|-- Dog
    Animal <|-- Cat
```

**类图关系：**
- `-->` - 关联
- `--|>` - 继承
- `..|>` - 实现
- `-->o` - 聚合
- `-->*` - 组合
- `..>` - 依赖

### 4. 状态图 (State Diagram)

```mermaid
stateDiagram-v2
    [*] --> 待处理
    待处理 --> 处理中: 提交
    处理中 --> 已完成: 完成
    处理中 --> 已取消: 取消
    已完成 --> [*]

    note right of 处理中: 处理中显示一些说明
```

### 5. 甘特图 (Gantt Chart)

```mermaid
gantt
    title 项目计划
    dateFormat  YYYY-MM-DD
    section 设计
    需求分析       :a1, 2024-01-01, 3d
    原型设计       :a2, after a1, 5d
    section 开发
    后端开发       :b1, 2024-01-09, 7d
    前端开发       :b2, 2024-01-10, 5d
    section 测试
    功能测试       :c1, after b1, 3d
```

### 6. 饼图 (Pie Chart)

```mermaid
pie title 项目时间分配
    "开发" : 40
    "测试" : 30
    "文档" : 20
    "其他" : 10
```

### 7. ER 图 (Entity Relationship)

```mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
    CUSTOMER {
        string name
        string address
        int id
    }
    ORDER {
        int id
        date date
        string status
    }
```

### 8. 导航图 (User Journey)

```mermaid
journey
    title 用户购物流程
    section 浏览商品
      查看商品详情: 5: 用户
      比较价格: 4: 用户
    section 购买
      加入购物车: 5: 用户
      结算: 3: 用户
      支付: 2: 用户
```

### 9. Git 图 (Git Graph)

```mermaid
gitGraph
    commit
    commit
    branch develop
    checkout develop
    commit
    checkout main
    merge develop
    commit
```

### 10. 思维导图 (Mindmap)

```mermaid
mindmap
  root((思源笔记))
    功能
      笔记管理
      块级引用
      SQL查询
    特性
      本地存储
      端对端加密
      跨平台
```

## 高级功能

### 子图 (Subgraphs)

```mermaid
graph TB
    subgraph 客户端
        A[用户界面]
        B[交互逻辑]
    end

    subgraph 服务器
        C[API 网关]
        D[业务逻辑]
    end

    A --> C
    B --> C
    C --> D
```

### 样式定制

```mermaid
graph TB
    classDef default fill:#f9f,stroke:#333,stroke-width:2px;

    A[开始] --> B{判断}
    B -->|是| C[执行]
    B -->|否| D[跳过]

    class A,C,D success
    class B decision

    classDef success fill:#90EE90,stroke:#333;
    classDef decision fill:#FFA500,stroke:#333;
```

### 注释

在 Mermaid 中使用 `%%` 添加注释：

```mermaid
graph TD
    %% 这是注释
    A[开始] --> B[处理]
    %% 另一条注释
    B --> C[结束]
```

## 在思源笔记中的特殊设置

思源笔记支持通过特殊语法设置 Mermaid 配置：

````
~~~mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#ffcccc'}}}%%

graph TD
A[开始] --> B[结束]
~~~
```

**可用主题：**
- `default` - 默认主题
- `forest` - 森林主题
- `dark` - 暗色主题
- `neutral` - 中性主题
- `base` - 基础主题（可完全自定义）

## 常见问题

### 图表不显示

1. 检查是否使用 `~~~mermaid` 而非 ` ```mermaid`
2. 确认思源笔记版本支持该图表类型
3. 检查语法是否正确

### 图表渲染缓慢

- 复杂图表可能需要几秒钟渲染
- 建议减少节点数量和关系复杂度

### 支持的 Mermaid 版本

思源笔记定期升级 Mermaid 版本，支持最新的功能和特性。

## 参考资源

- [Mermaid.js 官方文档](https://mermaid.js.org/)
- [思源笔记官方文档](https://siyuan-note.cn/)
- 思源笔记关于页：https://siyuannote.com/article/1725501271

## 示例集合

### 完整项目流程图

```mermaid
graph TD
    A[需求分析] --> B[设计阶段]
    B --> C[开发阶段]
    C --> D[测试阶段]
    D --> E[部署上线]
    E --> F[维护迭代]

    style A fill:#e1f5ff
    style B fill:#fff4e1
    style C fill:#FFE5E5
    style D fill:#f0f0f0
    style E fill:#e8f5e9
```

### 复杂时序图

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant API
    participant Service
    participant DB

    rect rgb(200, 223, 255)
        Note over Client,Service: 认证流程
        Client->>API: 登录请求
        API->>Service: 验证凭证
        Service->>DB: 查询用户信息
        DB-->>Service: 用户数据
        Service-->>API: 认证通过
        API-->>Client: 返回 Token
    end

    rect rgb(255, 223, 223)
        Note over Client,Service: 业务流程
        Client->>API: 业务请求 (带 Token)
        API->>Service: 处理业务
        Service-->>API: 处理结果
        API-->>Client: 业务响应
    end
```

---

**最后更新：2024-01**

如需了解更多详细信息，请查阅 [Mermaid 官方文档](https://mermaid.js.org/)