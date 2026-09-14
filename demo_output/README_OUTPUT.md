# 输出说明

规则分数不是校准概率；抽取和去重均需人工抽检。

搜索摘要、过期快照与合成样例不进入真实正文统计。

采集时间不等于职位发布日期或在招确认时间。

同句多能力分多行；原文偏移仅针对对应正文ID。

岗位资格与限制条款未计入证据映射覆盖率；该比率不是胜任度或录用概率。

先打开 dashboard.html，再读 requirements_zh.csv、review_queue.csv 与 hard_constraints.csv。

同一原文可能对应多项能力；精确原文见 requirements.jsonl，CSV 为防公式注入可能增加前导单引号。

输入快照与生成文件SHA256见 run_manifest.json。当前目录为一次运行的不可覆盖产物。
