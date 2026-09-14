# 可举证的岗位描述

> 合成样例演示：不是实际职位调研或个人成果。没有证据的内容只能作为待填写模板。

## 表述框架

场景与范围 → 自己承担的动作 → 工程控制机制 → 同口径指标 → 可追溯证据。

原文要求、岗位硬条件与限制条款保留在 CSV 中；以下措辞不能代替真实经历，也不保证完全胜任。

## 通用底座：待填模板

在【项目/业务场景及本人职责】中，以需求澄清、可执行规格、增量实现与验收证据组织 AI 辅助交付；对 AI 生成实现建立代码审查、自动化测试与失败样本回归机制；依据任务边界选择并使用【真实使用过的工具】，对生成代码执行理解、修改与验证；使用同口径基线、样本量和观察窗口评估交付周期、有效产出与成本；将业务目标拆解为规格、接口契约、任务边界和可执行验收标准，并维护项目上下文。在【同口径观察窗口】下，以【基线→当前值、样本量、质量约束】记录结果，并提供【提交/测试/发布/监控证据】。

## 样本要求并集：逐项措辞

### AI 原生工程交付

【待举证】以需求澄清、可执行规格、增量实现与验收证据组织 AI 辅助交付。量化字段：cycle_time_hours、accepted_change_rate.

对应要求：r_01d3137c3392609761729cd8, r_0eafabdeff78eb63ef3d95a4, r_16812b5204ce387ab6b2dedf, r_228beab0a50602ac3e8f947f, r_2345bc9fdfc0e4374877e479, r_327322d1ecd40ec4d9b407b0, r_4f5e4a2bf95e67474784143a, r_74b44925e13eeaae3eef1673, r_8705d98e9411c93b8ea0a17c, r_91863eb18b21e513ea364c6c, r_948b1297a2c5acab39da1e55, r_98a28d8687fc57cb21d1c6b5, r_99431ed843c1098fbdffdcc5, r_a5ae95cdcd5bfc41a7bfa51e, r_bdd388c5b4ff5f3ec816ac21, r_d2d8097c4eff1693e09c123e, r_e2a380adc9392f532f7c6a3d, r_ef8496c2dd7d5d36a4e2f26f, r_f7e941df1f032bd15d9e79fa, r_f8b936f563645b13e0037332。

### 测试、审查与可靠性

【待举证】对 AI 生成实现建立代码审查、自动化测试与失败样本回归机制。量化字段：test_pass_rate、reproducibility_rate.

对应要求：r_35728124253ef63f00275336, r_46742376e47cb7ddf7c3375b, r_80c227b54c8d0eb01014a458, r_ba2d0cfc8c144111c4528784, r_c9d9182d0c20cd5327c99ff5, r_ebaa547465afc1ad63225f91。

### 编码工具实战

【待举证】依据任务边界选择并使用【真实使用过的工具】，对生成代码执行理解、修改与验证。量化字段：accepted_change_rate、cost_per_accepted_change.

对应要求：r_16282886e794f4aa44696488, r_2f4d52c9ae84b960c3461b3c, r_370bc753f99b063e5c522132, r_3822cb18018ea660ef31543f, r_5175cc7a73c95125e6065ea7, r_5db99670b9a5e5cdef28393d, r_7bd0516a666f1ba46f4caf69, r_9e2b0e9492fe6495f25fbe8b, r_eae55c4197fae7fd89bf3ab3。

### 交付效率与成本度量

【待举证】使用同口径基线、样本量和观察窗口评估交付周期、有效产出与成本。量化字段：cycle_time_hours、cost_per_accepted_change.

对应要求：r_37036d6469a25beba17f078c, r_8665da0983cd7093d8d7c5dc, r_c41a7ed2895e152e242baf45, r_c91e7a845dd936e2967f5913。

### 规格与上下文工程

【待举证】将业务目标拆解为规格、接口契约、任务边界和可执行验收标准，并维护项目上下文。量化字段：first_acceptance_rate、cycle_time_hours.

对应要求：r_41a820c50728a0b5d0f28e8f, r_73dd19c7b433a76f5303ca17, r_7ccac33cac4d2de203e1ba14。

### Agent 编排与工具链

【待举证】通过任务分解、工具权限、执行反馈与人工检查点组织编码 Agent 协作。量化字段：agent_task_success_rate、cost_per_accepted_change.

对应要求：r_02deceae3868df8d30a5c682, r_47f5fb16c5829adef24f2704。

### 架构与技术治理

【待举证】围绕模块边界、接口契约、兼容约束与性能预算评审 AI 生成方案。量化字段：p95_latency_ms、cycle_time_hours.

对应要求：r_9a306ec10e8b82931ec768e3, r_fe89cc6bb8943fb299f1dc06。

### 发布、可观测与回滚

【待举证】将交付纳入版本管理、持续集成、可观测验证与可回滚发布流程。量化字段：availability_rate、rollback_minutes.

对应要求：r_947b7403b8b89c318f4d9086, r_e090a39a129227115375c697。

### 垂直领域与业务闭环

【待举证】将领域规则、数据口径与业务约束转化为可验证的建模及工程契约。量化字段：business_constraint_pass_rate、wape_pct.

对应要求：r_3ee5205f4c18079ef14cc439, r_64ef1abfed3b3722e5173d4a, r_87c688379f697669ec8f3384。

### 数据安全与使用边界

【待举证】明确数据、源码、密钥和工具权限边界，建立脱敏、审查与受控使用流程。量化字段：security_check_pass_rate.

对应要求：r_2879cf5ab651d41e1c0a21f0, r_f896f2cd5d4bd6eeb570fa78。

### 时间序列与评测方法

【待举证】在时间序列任务中落实预测时点、时间切分、防泄漏检查、滚动回测与误差分层评估。量化字段：wape_pct、reproducibility_rate.

对应要求：r_80bd8cd29386251e0f1b8d44, r_f61fb9842f94d6c4fdb2f7dc。

## 已经人工确认的候选人证据

### 合成交付流程演示 / 合成演示

来源：evidence/demo_measurement.json；人工确认人：DEMO_REVIEWER；证据状态：file_hash_verified_content_not_audited。

可比任务交付周期：10小时 → 6小时（相对下降40.00%）；当前样本量20；窗口合成窗口B；口径：仅演示计算公式；不是实际提效实验。

这些是输入方确认的观测值；文件哈希校验仅证明文件一致性，不证明内容真实，也不证明变化由 AI 单独导致。

