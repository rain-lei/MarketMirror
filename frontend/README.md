# MarketMirror Frontend (Vue 3 + Vite)

企业级监管沙盒前端控制台，支持多 Agent 行为仿真可视化、演示样例库、离线推演与自动连播。

## 技术栈

| 层 | 选型 |
|----|------|
| 框架 | Vue 3 (Composition API + SFC) |
| 构建 | Vite |
| UI 组件库 | Ant Design Vue |
| 可视化 | Apache ECharts (按需引入) |
| 路由 | 组件内状态切换（模块导航） |

## 目录结构

```
frontend/
├── index.html                          # Vite 入口
├── src/
│   ├── main.js                         # 应用挂载 & Ant Design 按需注册
│   ├── App.vue                         # 登录页 + 企业控制台 + 模块路由
│   ├── config.js                       # 应用常量、演示账号、API 地址
│   ├── components/
│   │   ├── MarketImpactChart.vue       # ECharts 冲击传播曲线
│   │   └── AgentDecisionMatrix.vue     # ECharts Agent 行为强度映射图
│   ├── services/
│   │   ├── api.js                      # 后端请求层（超时 18s + AbortController）
│   │   └── auth.js                     # 前端演示登录会话管理
│   ├── mock/
│   │   └── demoScenarios.js            # 内置样例库 + 离线推演引擎
│   └── styles/
│       └── app.css                     # 设计系统、布局与动画
└── README.md
```

## 模块能力

### 1. 账号登录
- 账号密码校验，错误码分类提示
- localStorage 会话持久化，刷新不丢失
- 一键填充演示账号

### 2. 场景推演（核心模块）
- 冲击事件描述 + 目标行业输入
- 演示样例库：3 个内置场景，卡片化展示（冲击级别、时间视角、风险信号标签）
- 启动推演：调用后端 `/api/simulate`
- 离线演示：后端不可用时本地生成结果
- 自动连播演示：每 7 秒自动切换样例并推演
- 讲解稿一键复制，适配路演提词

### 3. 风险预警
- 系统性风险评分、流动性压力、异常交易信号（当前占位，可扩展）

### 4. 审计中心
- 操作轨迹时间线，记录登录、推演、异常等全量事件

### 5. 平台配置
- 展示技术栈、API 地址等运行环境信息

### 6. 可视化
- 冲击传播曲线：12 期趋势模拟，正/负冲击双色主题
- Agent 行为强度映射：置信度 + 策略强度分组柱状图

## 本地运行

```powershell
npm install
npm run dev
```

默认访问 `http://127.0.0.1:5173`

## 环境变量

在 `frontend/` 目录下创建 `.env`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 演示账号

- `admin / Admin@2026` — 监管管理员
- `analyst / Analyst@2026` — 风险分析师
