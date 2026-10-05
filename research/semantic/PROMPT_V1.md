# 语义抽取提示规范 v1

输入是一个条目的 `segments`：提问阶段只有 `question`，回复阶段有已可见的 `question` 和 `reply`。只依据这些文本，不查外部知识，不推断公司财务、违规标签或未来股价。回复阶段优先识别公司回复对问题中说法的确认或否认。问题中的猜测不能当作已经发生的利好或利空。

只输出一个 JSON 对象，且只有 `events` 字段。没有可验证的具体事件时输出 `{"events":[]}`。每个事件含 `event_type`（regulation/liquidity/earnings/governance/other）、`direction`（positive/negative/neutral/unknown）、`affected_industries`（无证据则空数组）、`horizon`（short/medium/long/unknown）、0～1 的 `intensity` 与 `uncertainty`，以及至少一个 `evidence_spans`。证据字段为 `source`（question/reply）、0 起始字符索引 `start`、不包含终点的 `end` 和精确原文 `quote`。若无法找到精确跨度，则不输出该事件。不要输出解释文字或 Markdown。

执行器会在本地把模型 ID、提示版本、源文本 SHA-256 和原始响应同结构化事件绑定。JSON 格式错误、超出可见文本的证据、字段缺失或类型错误会记录为解析失败，而不是修补或替模型猜测。该提示规范尚未经过人工标注集验证。
