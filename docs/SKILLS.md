# 技能场景（Skills）

赫尔墨斯把「技能」(`Skill`) 设计成 **一段 system_prompt + 一组关键词**：
选中某个技能后，所有问答会在 LLM 的 system 消息前面拼上这段 prompt，
把通用助手调成某个领域专家（财务、会议纪要、政策解读、发票核验、
科研写作 …）。本文档说明三件事：

1. 怎么用技能（用户视角）。
2. 怎么管理 / 导入技能（管理员视角）。
3. 怎么自己写一个技能（创作者视角）。

---

## 1. 是什么

一条 `Skill` 记录由 4 个有效字段组成（其余字段已废弃，见 §8）：

| 字段                   | 类型     | 作用                                          |
|------------------------|---------|-----------------------------------------------|
| `name`                 | str     | 唯一名称，前端展示用                            |
| `description`          | str     | 一句话介绍，前端 hover / 推荐时显示              |
| `system_prompt`        | str     | **核心**：会被拼到 LLM system 消息前缀           |
| `icon`                 | emoji   | 单字符图标（📊 📝 📋 🧾 📚 …）                  |
| `auto_detect_patterns` | str[]   | 关键词列表，命中即给该技能 +1 分（自动识别用） |

启动时 `init_skills()` 会幂等 seed 5 个预置技能：

| id | name           | icon | 触发关键词举例           |
|----|----------------|------|--------------------------|
| 1  | 财务分析       | 📊   | 财务、报表、利润 …       |
| 2  | 会议纪要助手   | 📝   | 会议、纪要、议程 …       |
| 3  | 本地政策文档   | 📋   | 政策、规定、条例 …       |
| 4  | 发票助手       | 🧾   | 发票、invoice、税额 …    |
| 5  | 科研写作助手   | 📚   | 论文、文献综述、LaTeX …  |

第 5 个 demo 改编自
[Norman-bury/research-writing-skill](https://github.com/Norman-bury/research-writing-skill)
，详见 §5。

---

## 2. 怎么用（用户视角）

### 2.1 UI 视角

1. 新建对话时下拉选择技能 → `Conversation.skill_id` 落库。
2. 之后所有发往该对话的消息（同步 `/messages` 或流式 `/messages/stream`）
   后端都会自动把 `Skill.system_prompt` 拼进 LLM 的 system 消息前缀。
3. 想中途切换技能：直接修改对话的 `skill_id`，**不会**清空对话历史；
   如果旧角色已污染上下文，建议新建对话。

### 2.2 API 视角

`POST /query` 与 `POST /api/conversations/{id}/messages*` 的请求体都
支持 `skill_id`。优先级：

```
request.skill_id  >  conversation.skill_id  >  无 skill
```

例：

```bash
curl -N -X POST http://localhost:8000/api/conversations/42/messages/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"帮我润色这段摘要","skill_id":5}'
```

### 2.3 自动识别

`POST /api/skills/detect` 传 `filename` 和/或 `content`，返回得分最
高的技能 id：

```bash
curl -X POST http://localhost:8000/api/skills/detect \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"我的硕士论文_v3.docx"}'
# → {"suggested_skill_id": 5, "skill_name": "科研写作助手"}
```

打分极简：在 `filename + " " + content` 中出现一个关键词就 +1，
最高分胜出，平分按 `Skill.id` 升序（先注册的优先）。无任何命中
返回 `null`，前端不弹推荐横幅。

---

## 3. 管理员管理（admin）

所有路径都需要 admin 身份（`Authorization: Bearer <admin_token>`）。

```bash
# 列表
curl -X GET .../api/skills

# 详情
curl -X GET .../api/skills/5

# 新建
curl -X POST .../api/admin/skills -d '{
  "name":"合同审查助手",
  "description":"快速识别合同条款风险",
  "system_prompt":"你是一个企业法务助手，请逐条评估合同条款 ...",
  "icon":"⚖️",
  "auto_detect_patterns":["合同","甲方","乙方","条款","违约"]
}'

# 部分更新（任何字段省略 = 不改）
curl -X PUT .../api/admin/skills/6 -d '{"icon":"📜"}'

# 删除
curl -X DELETE .../api/admin/skills/6
```

字段语义：

- `name` 全局唯一，重名直接 409。
- `system_prompt` 必填且 `min_length=1`。
- `icon` 建议单 emoji；多字符不报错但前端可能换行。
- `auto_detect_patterns` 关键词命中阈值 = 1，**没有「至少 N 命中」**
  这种配置；想严格匹配请用更独特的关键词。

---

## 4. 从 SKILL.md 一键导入

社区里常见的「技能资源」是带 frontmatter 的 markdown 文件。手填
`system_prompt` 几百字太痛苦，所以提供了导入端点：

`POST /api/admin/skills/import`

```bash
curl -X POST .../api/admin/skills/import \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "markdown": "---\nname: code-reviewer\ndescription: 严格的代码评审\n---\n\n你是一个严格的高级工程师 ...",
  "icon": "🧐",
  "auto_detect_patterns": ["review","代码评审","PR","pull request"],
  "overwrite": false
}
JSON
```

### 解析规则

- 仅识别**单行** `key: value` 形式的 frontmatter，**不支持**嵌套
  YAML、list、多行 string（这是项目零依赖换来的取舍）。
- 自动取的字段：`name`、`description`。
- frontmatter 之后的所有内容整体作为 `system_prompt`。
- `name` 缺失时必须传请求体的 `name_override`，否则 422。

### 最小模板

```markdown
---
name: my-skill
description: 一句话说明这个技能干什么
---

你是一个 ⟨某领域⟩ 专家。请遵循以下原则：
1. ...
2. ...

回答时使用简练中文，引用必须可追溯。
```

### 冲突策略

| 同名已存在 | `overwrite` | 行为                                    |
|-----------|-------------|-----------------------------------------|
| 否        | —           | 新建，返回 201                          |
| 是        | `false`     | 返回 409，提示开 overwrite              |
| 是        | `true`      | 按 id 更新 description / system_prompt  |

---

## 5. research-writing demo

第 5 个预置技能改编自
[Norman-bury/research-writing-skill](https://github.com/Norman-bury/research-writing-skill)
，原仓库是一个 19 个子技能的多文件 bundle，依赖 Claude Code / Cursor /
Codex 这类 agentic 平台的 `Skill` 工具做动态调度。

本项目的 Skill 模型是**单 system_prompt**，无法表达多步工作流，因此
demo 把 `SKILL.md` 的「哲学原则」「Red Flags」「核心规则」三段精炼
成 ~30 行的单 prompt：

- **流程优于即兴**：先确认论文类型与章节结构，再写。
- **证据优于声称**：所有引用必须可追溯，资料中无依据时如实说。
- **简洁优于复杂**：去 AI 化，避免「值得注意的是」「综上所述」类
  机械表达。
- **确认优于假设**：每章节写完请用户确认再继续。

### 想用完整 19 个子技能怎么办？

把上游每个子技能（`brainstorming-research`、`writing-chapters`、
`literature-review` 等）的 `SKILL.md` 分别用 §4 的 import 端点
导入成独立 Skill；切换对话技能 = 切换工作阶段。这是当前项目支持
的最接近原版的做法。

---

## 6. 自己写一个技能

写好 `system_prompt` 是一件「prompt 工程 + 领域专家」的事，给一份
经验清单：

### 6.1 system_prompt 写法清单

1. **角色定位**：开头一句明确「你是一个 ⟨什么⟩ 专家」。
2. **能力边界**：列 3-5 条「该做什么 / 不该做什么」，例如「不编造
   文献」「不替用户做财务决策」。
3. **引用纪律**：明确「资料中无依据时必须说『资料中未涉及』」。
   这点对 RAG 场景尤其重要，能极大降低幻觉率。
4. **输出格式**：要求列表 / 表格 / 编号步骤；明确语言（简练中文）。
5. **长度控制**：~80 行内最佳；超过会挤占上下文窗口、拖慢首 token。

### 6.2 auto_detect_patterns 调参建议

- **关键词越独特越好**。`"会议"` 这种通用词会误命中政策、合同等
  无关文档。预置技能里的「会议纪要助手」用了 `["会议","纪要",
  "议程","决议","讨论","参会","待办","议题","记录"]` —— 多个
  关键词共同抬高得分，单个误命中不致引起推荐。
- **避免与其他 skill 冲突**：detect 是按命中数排名，平分按 id 升序；
  如果两个 skill 共享「合同」关键词，先注册的总赢。建议新增 skill
  前用 `GET /api/skills` 看一下其他 skill 的 patterns。
- **不要过长**：超过 30 个关键词一来无意义，二来增加误命中概率。

---

## 7. 调试技巧

1. **验关键词是否生效**：用 `POST /api/skills/detect` 直接打分。
   ```bash
   curl -X POST .../api/skills/detect -d '{"content":"季度营收同比增长 12%"}'
   ```
2. **A/B 同问题切技能**：用 `POST /query` 传不同 `skill_id`，对比
   两次 `answer` 是否真的体现技能差异。
3. **流式排错**：如果觉得「选了技能但流式回答没变化」，按这个顺序查：
   - request body 里有 `skill_id` 吗？没有就 fall back 到 `conv.skill_id`；
   - `Conversation` 行的 `skill_id` 真的写进库了吗？查 SQLite：
     `SELECT skill_id FROM conversations WHERE id = ?`；
   - 该 `Skill.system_prompt` 真有内容吗？管理后台漏填会落空字符串；
   - 临时在 `rag_engine._build_rag_prompt` 头部加 `logger.debug(sys_prompt)`
     一次性确认（验完务必删，避免泄漏 admin 配置）。
4. **多 skill 关键词冲突**：用 `GET /api/skills` 拉所有 skill，把
   `auto_detect_patterns` 全部 dump 出来，找重复词。

---

## 8. 已知局限

- **`rules` 字段已废弃**：DB 列保留以兼容老库，但 schema、API 响应、
  rag_engine 全都不再读写。写进去什么都不会发生。
- **单 system_prompt 不表达工作流**：上游 GitHub 上的多文件 skill
  bundle（如 research-writing 的 19 个子技能）需要拆成多个 Skill
  各自 import，靠用户切换对话技能切换阶段。
- **frontmatter 解析仅支持单行 KV**：list、嵌套对象、多行 string
  统统解析失败。需要这些时请手工拆完后再 import。
- **Skill 切换不清空历史**：旧角色的对话上下文仍在，可能与新技能
  冲突；建议新建对话。
- **prompt 长度无硬限制**：写得太长会挤占 LLM 上下文窗口、显著拖慢
  首 token。控制在 80 行内为宜。
- **detect 算法极简**：纯关键词命中数，不涉及 embedding 相似度；
  对同义词、英语缩写不敏感。复杂场景请配合 §6.2 的关键词调优。
