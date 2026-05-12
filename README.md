# MarketMirror — 多类型投资者市场冲击仿真平台

MarketMirror 是一款面向金融监管研究的**智能沙盒推演系统**，通过模拟三类不同风险偏好的投资者（激进型、保守型、机构型）在面对市场冲击事件时的差异化决策行为，量化评估政策或突发事件对特定行业造成的市场冲击。

## 核心能力

| 能力层 | 说明 |
|--------|------|
| **多 Agent 行为仿真** | 三类投资者（R/I/C）基于冲击信号独立决策，输出 BUY/SELL/HOLD 及置信度 |
| **文本信号解析** | 将政策描述/突发事件文本转化为可量化的市场冲击变量 |
| **冲击量化评估** | 输出 `market_impact_score`（市场冲击指数）、冲击方向（上行/下行/中性）、场景标签 |
| **可视化决策链路** | ECharts 冲击传播曲线 + Agent 行为强度映射图，全链路可追溯 |
| **离线演示模式** | 无需后端即可稳定生成推演结果，适配现场答辩、路演等无网络场景 |
| **企业级控制台** | 账号登录、模块化导航、审计日志、自动连播演示、讲解稿一键复制 |

## 技术架构

```
┌─────────────────────────────────────────────────┐
│                    Frontend                      │
│         Vue 3 + Vite + Ant Design Vue            │
│              Apache ECharts 可视化                │
├─────────────────────────────────────────────────┤
│                  API Layer                       │
│        POST /api/simulate   GET /api/health      │
├─────────────────────────────────────────────────┤
│                    Backend                       │
│           FastAPI + Pydantic + Uvicorn           │
│         规则引擎: 信号解析 · 冲击计算 · Agent决策  │
└─────────────────────────────────────────────────┘
```

## 仓库结构

```
demo/
├── backend/                  # FastAPI 服务
│   ├── main.py               # API 入口、仿真核心逻辑
│   ├── requirements.txt      # Python 依赖
│   └── .env.example          # 环境变量模板
├── frontend/                 # Vue 3 企业级前端
│   ├── index.html            # Vite 入口
│   ├── src/
│   │   ├── App.vue           # 主组件：登录、布局、模块路由
│   │   ├── config.js         # 应用常量、演示账号、API 地址
│   │   ├── main.js           # Vue 挂载 & Ant Design 按需注册
│   │   ├── components/
│   │   │   ├── MarketImpactChart.vue      # 冲击传播曲线（ECharts）
│   │   │   └── AgentDecisionMatrix.vue    # Agent 行为强度映射图（ECharts）
│   │   ├── services/
│   │   │   ├── api.js         # 后端请求层（含超时与异常处理）
│   │   │   └── auth.js        # 前端演示鉴权与会话管理
│   │   ├── mock/
│   │   │   └── demoScenarios.js  # 内置样例库与离线推演引擎
│   │   └── styles/
│   │       └── app.css        # 设计系统与动画
│   └── PLATFORM_DESIGN.md     # 平台设计说明
└── README.md
```

## 快速启动

### 1) 启动后端

```powershell
cd backend
pip install -r requirements.txt
python main.py
```

默认监听 `http://127.0.0.1:8000`

### 2) 启动前端

```powershell
cd frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`

## API 接口

### `GET /api/health` — 健康检查

### `POST /api/simulate` — 执行沙盒推演

请求体：

```json
{
  "event_description": "2020年初突发新冠疫情全球蔓延，全球供应链受阻",
  "target_industry": "房地产行业"
}
```

响应体：

```json
{
  "policy_parsed": "Parsed Policy: ...",
  "market_impact_score": -0.72,
  "scenario_tag": "pandemic-shock:real-estate",
  "agent_actions": [
    {
      "agent_type": "Aggressive (激进型)",
      "action_type": "Buy on Dip / 抄底",
      "confidence": 0.82,
      "reasoning": "High risk tolerance, seeking alpha in volatility."
    }
  ],
  "timestamp": 1714600000.0
}
```

## 投资者 Agent 模型

| 类型 | 标识 | 风险偏好 | 典型行为 | 决策逻辑 |
|------|------|----------|----------|----------|
| **激进型** | Aggressive (R) | 高风险容忍 | 抄底、做空波动、加杠杆做多 | 逆向交易，在波动中放大仓位 |
| **保守型** | Conservative (C) | 本金优先 | 恐慌抛售、持币观望 | 降低风险暴露，优先保全资本 |
| **机构型** | Institutional (I) | 风控驱动 | 对冲风险、板块轮动 | 基于风控模型执行再平衡 |

## 内置演示样例

| 场景 | 冲击级别 | 时间视角 | 目标行业 |
|------|----------|----------|----------|
| 2018 去杠杆冲击 | 高 | 中期 (3-6个月) | 房地产行业 |
| 2020 疫情突发 | 极高 | 短期 (1-3个月) | A股主板 |
| 平台经济监管升级 | 中 | 中长期 (6-12个月) | 科技行业 |

## 环境配置

后端环境变量（参考 `backend/.env.example`）：

- `MARKETMIRROR_HOST` — 监听地址，默认 `0.0.0.0`
- `MARKETMIRROR_PORT` — 监听端口，默认 `8000`
- `MARKETMIRROR_CORS_ORIGINS` — CORS 允许来源，逗号分隔

前端环境变量（`frontend/.env`）：

- `VITE_API_BASE_URL` — 后端 API 地址，默认 `http://127.0.0.1:8000`

## 演示账号

- `admin / Admin@2026` — 监管管理员
- `analyst / Analyst@2026` — 风险分析师

## 演示流程建议

1. 登录后进入「场景推演」
2. 在演示样例库中点击任意样例卡片，自动填充冲击事件与目标行业
3. 点击「启动推演」调用后端 API，或点击「离线演示」直接生成结果
4. 依次展示：市场影响指数 → 冲击传播曲线 → Agent 行为强度映射 → Agent 决策路径明细
5. 点击「自动连播演示」循环切换样例，适合展台无人值守展示
6. 点击「复制讲解稿」导出演讲提词器文本

## 下一步演进

- [ ] 接入 LLM 文本信号抽取服务，替换规则引擎
- [ ] 支持多轮迭代仿真与时间序列建模
- [ ] 后端 JWT/OAuth2 鉴权替换前端演示登录
- [ ] 接入真实风控指标与审计日志系统
