# GPT 生图自定义尺寸实施计划

## 1. Domain and structured action

- [x] 在 `logic/models.py` 定义单边最小/最大值、总像素上限和默认目标尺寸。
- [x] 将 `ImageAction.size` 替换为末尾的可空 `width`、`height`，集中校验成对、严格整数、
  单边范围与总像素。
- [x] 在 `_ImageActionOutput` 增加必需但可空的宽高并复用领域边界常量。
- [x] 让 `parse_chat_agent_output` 原样传递宽高。
- [x] 更新 `.agents/skills/aiqq-gpt-image/SKILL.md`，明确 AI 决策、`null/null` 回退和非法输出。
- [x] 扩充 Schema、Agent 和 skill 注入测试。

## 2. Workflow ownership

- [x] 移除 GPT 工作流对图片审核 `orientation` 的尺寸映射和 action 覆盖。
- [x] 普通聊天生成与编辑保留 skill 输出的宽高。
- [x] `/GPT生图` 创建无宽高 action，由服务边界默认 `1024x1024`。
- [x] 恢复审核方向的非空三值契约，保持 NovelAI 原有方向行为。
- [x] 更新工作流测试，覆盖自定义、默认、生成、编辑和审核不覆盖尺寸。

## 3. Request selection and exact output

- [x] 用单一 resolver 根据目标长宽比确定最近的原生 GPT 请求尺寸，并固定平局规则。
- [x] 让旧 Images API 适配器使用原生请求尺寸、独立目标画布。
- [x] 让 Responses 适配器使用同一解析结果，并在 502 重试期间保持不变。
- [x] 让 `_decode_and_normalize` 精确输出目标宽高 JPEG 和对应 `ImageAsset` 元数据。
- [x] 覆盖 `1920x1080`、`1080x1920`、`800x800`、默认值与比例阈值测试。

## 4. Regression and documentation

- [x] 更新 `README.md`，说明 skill 自定义尺寸、默认值、边界和原生尺寸限制。
- [x] 更新 `.trellis/spec/backend/image-services.md`，记录结构化动作、最近比例请求和目标画布契约。
- [x] 审查所有 `ImageAction(`、`orientation`、`size` 和规范化调用点，避免遗留上一版方向方案。
- [x] 运行尺寸相关定向单元测试（86 项通过）。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests/unit`（280 项通过）。
- [x] 运行 `.venv/bin/python -m compileall -q src tests/unit`。
- [x] 运行 `git diff --check` 并复核最终工作区范围。

## Final Validation

- 2026-09-17：`.venv/bin/python -m unittest discover -s tests/unit`，280 项通过。
- 2026-09-17：`.venv/bin/python -m compileall -q src tests/unit`，通过。
- 2026-09-17：`git diff --check`，通过。
- 最终变更仅覆盖 skill、结构化动作、GPT 图片适配器、相应测试、README、后端规范和任务文档。
- 未启动、停止或重启服务；未部署；未发起真实或付费生图请求。

## Validation Constraints

- 不执行真实或付费生图。
- 不启动、停止或重启服务，不部署。
- 全仓旧版 `bot.AiQQBot` 测试若仍因当前 `HEAD` 已有缺失而导入失败，单独记录，不扩大本任务。

## Rollback Points

- Schema/skill 阶段失败：恢复原 `image_action` 字段，不触碰图片适配器。
- 适配器阶段失败：恢复默认方图请求与画布，保留现有错误、重试和额度行为。
- 不使用 `git reset --hard`，不回滚工作区中无法确认归属的其他改动。
