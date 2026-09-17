# GPT 生图自定义尺寸设计

## Architecture

把目标尺寸放在普通聊天已经存在的生图结构化动作中。图片审核仍是安全边界，但不再是 GPT
尺寸决策者。

```text
用户消息与群历史
  -> Chat Agent + aiqq-gpt-image skill
       image_action.width: int | null
       image_action.height: int | null
  -> strict schema + ImageAction validation
       null/null -> target 1024x1024
       valid ints -> exact target dimensions
  -> image prompt audit (safety/effectiveness only for GPT)
  -> resolve native request size from nearest aspect ratio
       square    -> 1024x1024
       portrait  -> 1024x1536
       landscape -> 1536x1024
  -> CodexResponsesImageService / GPTImageService
  -> decode, contain and pad to exact target dimensions
  -> ImageAsset with matching width and height
```

## Contracts

### Domain action

`logic/models.py` owns the shared bounds and validates runtime construction:

```python
MIN_GPT_IMAGE_DIMENSION = 256
MAX_GPT_IMAGE_DIMENSION = 2048
MAX_GPT_IMAGE_PIXELS = 4_194_304
DEFAULT_GPT_IMAGE_DIMENSIONS = (1024, 1024)

@dataclass(frozen=True)
class ImageAction:
    mode: ImageActionMode
    prompt: str
    source_record_id: int | None = None
    width: int | None = None
    height: int | None = None
```

`width` 和 `height` 放在现有字段之后，保留历史位置参数含义。校验规则为：

- 二者同时为 `None`，或同时为严格整数；拒绝 Python `bool`。
- 每边 `256..2048`。
- 乘积不超过 `4,194,304`。

提供 `target_dimensions` 只读属性或等价纯函数，把 `None/None` 解析为默认值，避免工作流和
适配器复制回退逻辑。

### Structured chat output

`_ImageActionOutput` 增加必需但可空的严格整数字段：

```python
width: int | None = Field(ge=256, le=2048)
height: int | None = Field(ge=256, le=2048)
```

模型验证器强制成对出现并检查总像素。解析器将值原样传给 `ImageAction`。Pydantic Schema 中
两字段保留在 `required`，因此 JSON `null` 表示“AI 未决定”，缺字段表示协议错误。

### Skill policy

`aiqq-gpt-image/SKILL.md` 指导聊天 AI：

- 明确尺寸请求优先按用户值填写。
- 可依据海报、壁纸、头像等用途选择合法尺寸。
- 无可靠精确尺寸时填 `null/null`，由应用默认方图。
- 不输出 `auto`、尺寸字符串、单边尺寸或越界值。

Skill 只构造结构化动作，不调用图片接口，也不做图片后处理。

## Native Size Selection

服务层共享一个解析函数，同时返回上游尺寸和目标画布：

```python
def resolve_gpt_image_sizes(
    action: ImageAction,
) -> tuple[GPTImageSize, tuple[int, int]]: ...
```

对目标比例 `width / height` 和三个原生比例做对数距离比较：

```python
abs(log(target_ratio / native_ratio))
```

对数距离能让横竖倒数比例对称。候选顺序固定并在距离相同时优先 square，保证跨适配器、重试和
测试结果确定。默认目标直接解析为 `1024x1024`。

## Workflow Integration

### Ordinary chat generation and edit

`parse_chat_agent_output` 创建携带 `width`、`height` 的 `ImageAction`。
`ConversationWorkflow._execute_image_action` 完成图片审核后直接保留该 action，不再用审核方向
`replace` 尺寸。生成与编辑走相同服务入口。

### Explicit `/GPT生图`

`GPTImageWorkflow` 创建不带宽高的 `ImageAction`，服务边界解析为 `1024x1024`。命令提示词
中的数字仍只是画面描述，不新增尺寸语法。

### Image audit and NovelAI

恢复 `ImagePromptAuditResult.orientation` 的原有非空三值契约。GPT 工作流忽略其方向，
NovelAI 继续使用它选择既有尺寸，避免把 GPT 自定义尺寸规则泄漏到另一提供商。

## Adapter and Normalization

- 两个 GPT 适配器在任何网络操作前调用同一个尺寸解析函数。
- 请求参数使用解析出的原生 `GPTImageSize`。
- `_decode_and_normalize` 接收独立的目标 `(width, height)`，以该尺寸创建白色画布。
- 返回元数据来自目标画布，而非上游尺寸或响应声明。
- Responses SSE 不依赖事件中的 `size`；实际图片仍需成功解码，再按已验证目标画布规范化。
- 每次 502 重试复用初次解析的原生尺寸和目标尺寸。

## Compatibility and Failure Behavior

- 历史 `ImageAction(mode, prompt, source_record_id)` 保持有效并默认方图。
- 结构化输出缺失、部分或非法宽高由严格 Schema 拒绝，沿用聊天 Agent 输出无效路径。
- 运行期直接构造非法 `ImageAction` 时由领域对象立即拒绝，不消耗额度、不发送网络请求。
- 图片审核拒绝、安全失败、参考图失败、额度和图片接口错误行为不变。
- QQ 上传准备仍可为 2 MiB 限制压缩或缩小服务输出，这是既有交付边界，不回写 GPT
  `ImageAsset` 的尺寸契约。

## Test Strategy

1. Skill/Schema：断言宽高字段、required-nullable JSON Schema、null、边界、部分值、错误类型、
   越界和超像素。
2. Domain：覆盖默认、合法任意尺寸、边界和所有非法组合。
3. Workflow：普通聊天生成/编辑保留尺寸，审核方向不覆盖；显式命令默认方图；NovelAI 不变。
4. Resolver：覆盖方、横、竖、接近阈值和确定性平局。
5. Responses adapter：断言上游原生尺寸与最终任意画布分离，生成/编辑和重试一致。
6. Legacy adapter：做相同请求与实际 JPEG/元数据断言。
7. Regression：运行相关测试、全部 `tests/unit`、`compileall` 和 `git diff --check`。

## Rollback

回滚本任务的 skill、Schema、领域字段、工作流、适配器、测试和规范变更即可。无数据库迁移、
环境变量或部署步骤；不得覆盖同一工作区其他活跃任务的改动。
