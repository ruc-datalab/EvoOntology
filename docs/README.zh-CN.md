<p align="center">
  <a href="README.md">English</a> | 简体中文
</p>

# EvoOntology 文档

EvoOntology 维护供 Data Agent 查询的版本化本体层。Build / Evolve skills 负责构建与更新，确定性核心负责存储、运行时访问和进化生命周期；候选更新在轮次预算与评测边界内经过 Parent/Candidate 比较，只有 Accept 才发布新版本。

## 文档导航

- [架构总览](architecture.zh-CN.md) —— 本体层结构、闭环、模块职责、两种 mode 与 benchmark 接入。
- [接入一个新的 Benchmark](guide/new-benchmark.zh-CN.md) —— 数据加载、任务执行与评分、adapter 契约、配置及初始本体构建。
- [使用指南](../USAGE.zh-CN.md) —— 安装、workspace、进化闭环、端到端流程。
