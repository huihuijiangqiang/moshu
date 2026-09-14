"""Deterministic runtime writing-skill selection for novel generation.

These skills are application prompt modules, not Codex ``SKILL.md`` files.  Keeping
them as versioned data makes selection auditable and lets tests detect accidental
prompt drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WritingSkill:
    id: str
    version: str
    category: str
    priority: int
    prompt: str


@dataclass(frozen=True)
class SkillSelection:
    skills: tuple[WritingSkill, ...]
    scene: str

    @property
    def ids(self) -> list[str]:
        return [skill.id for skill in self.skills]

    def prompt(self) -> str:
        return "\n\n".join(f"[{skill.category}:{skill.id}@{skill.version}]\n{skill.prompt}" for skill in self.skills)


# These are deliberately different story engines, rather than cosmetic scene
# labels.  A chapter can still use the author's explicit outline; the catalog
# only supplies a concrete default when a legacy/short outline has no dramatic
# contract of its own.
CHAPTER_VARIATION_CATALOG: tuple[dict[str, str], ...] = (
    {
        "engine": "公开对峙",
        "carrier": "名誉与围观者的判断",
        "hook": "倒计时",
        "hook_shape": "让一个有权力的人当众启动期限或处罚，章尾停在期限已经开始减少的瞬间。",
        "instruction": "让主角在众目睽睽下先占据主动，再被对手把时间压力压到现场；章尾让倒计时真实开始。",
    },
    {
        "engine": "移动追索",
        "carrier": "正在转移的人、物或路线",
        "hook": "未完成动作",
        "hook_shape": "让目标已经离开原位并进入一条未知路线，章尾停在追踪动作已经启动、去向尚未确认的瞬间。",
        "instruction": "让线索随着人物移动而改变，主角必须边追边舍弃一项资源；章尾停在一个已经启动、却无法收回的动作上。",
    },
    {
        "engine": "关系交换",
        "carrier": "盟友的援手与附带条件",
        "hook": "关系威胁",
        "hook_shape": "让盟友或亲近者当场撤回援手、提出交换或转向对手，章尾停在关系代价已经落下的动作上。",
        "instruction": "让双方都握有底线和筹码，关系变化由一次具体选择造成；章尾让援手撤回、变质或提出更高代价。",
    },
    {
        "engine": "资源争夺",
        "carrier": "正在减少的粮、钱、位置或时间",
        "hook": "两难选择",
        "hook_shape": "让两种损失同时变成眼前事实，章尾停在主角必须立刻舍弃其中一项的选择前。",
        "instruction": "让稀缺资源在场景中被看见并持续减少，主角只能保住一端；章尾明确呈现必须二选一的损失。",
    },
    {
        "engine": "身份错位",
        "carrier": "一件与身份或旧记录矛盾的证物",
        "hook": "身份偏差",
        "hook_shape": "让一件可触摸、可核对的证物与已知身份冲突，章尾停在证物被翻出或落入他人手中的瞬间。",
        "instruction": "先让主角依据既有身份行动，再用可见证物击穿判断；章尾留下一个身份矛盾，不能当场解释完。",
    },
    {
        "engine": "密室调查",
        "carrier": "受限空间里的证据缺口",
        "hook": "证据缺口",
        "hook_shape": "让关键证据被刮掉、转移或被别人先拿走，章尾停在缺口已经造成且无法立即补回的瞬间。",
        "instruction": "限制人物的进出和信息来源，按观察、假设、验证推进；章尾让关键证据被刮掉、转移或落入他人手中。",
    },
    {
        "engine": "对手先手",
        "carrier": "对手已经执行的不可逆行动",
        "hook": "对手新行动",
        "hook_shape": "让对手先完成一项不可逆行动并产生现场后果，章尾停在后果扩散到主角面前的瞬间。",
        "instruction": "不要等主角安排好再出事，先让对手完成一项改变局面的行动；章尾展示后果正在扩散。",
    },
    {
        "engine": "情绪决裂",
        "carrier": "亲密关系中的信任边界",
        "hook": "突然揭示",
        "hook_shape": "让一条会重写关系的事实通过动作、证物或一句话落地，章尾停在对方已经做出回应但真相未尽的瞬间。",
        "instruction": "用一次失信、隐瞒或越界推动情绪转向，避免靠旁白解释感情；章尾揭出一条会重新定义关系的新事实。",
    },
)


def _select_variation_profile(
    chapter_index: int,
    *,
    explicit_hook: str = "",
    recent_patterns: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Choose a least-recently-used story engine and hook pair.

    Keeping the choice in one place is important: the prompt contract and the
    internal five-beat blueprint must describe the same chapter, otherwise a
    model can satisfy one and accidentally ignore the other.
    """
    start = max(0, chapter_index - 1) % len(CHAPTER_VARIATION_CATALOG)
    recent = recent_patterns or []
    recent_engines = [
        str(item.get("variationEngine") or "").strip()
        for item in recent
        if str(item.get("variationEngine") or "").strip()
    ]
    recent_hooks = [
        str(item.get("actualHookType") or item.get("hookType") or "").strip()
        for item in recent
        if str(item.get("actualHookType") or item.get("hookType") or "").strip()
    ]
    engine_counts = {
        candidate["engine"].casefold(): sum(
            value.casefold() == candidate["engine"].casefold() for value in recent_engines
        )
        for candidate in CHAPTER_VARIATION_CATALOG
    }
    hook_counts = {
        candidate["hook"].casefold(): sum(
            value.casefold() == candidate["hook"].casefold() for value in recent_hooks
        )
        for candidate in CHAPTER_VARIATION_CATALOG
    }
    previous_engine = recent_engines[-1].casefold() if recent_engines else ""
    used_engine_keys = {value.casefold() for value in recent_engines}
    used_hook_keys = {value.casefold() for value in recent_hooks}
    ranked: list[tuple[tuple[int, int, int], dict[str, str]]] = []
    for offset in range(len(CHAPTER_VARIATION_CATALOG)):
        candidate = CHAPTER_VARIATION_CATALOG[(start + offset) % len(CHAPTER_VARIATION_CATALOG)]
        engine_key = candidate["engine"].casefold()
        hook_key = candidate["hook"].casefold()
        ranked.append(
            (
                (
                    int(engine_key in used_engine_keys)
                    + int(not explicit_hook and hook_key in used_hook_keys),
                    engine_counts[engine_key]
                    + (0 if explicit_hook else hook_counts[hook_key]),
                    int(engine_key == previous_engine),
                ),
                candidate,
            )
        )
    ranked.sort(key=lambda item: item[0])
    selected = dict(ranked[0][1])
    if explicit_hook:
        selected["hook"] = explicit_hook
        selected["hook_shape"] = (
            "严格兑现作者指定的钩子类型，但必须通过具体人物、对象和已启动动作落地，"
            "不能只用旁白宣布悬念。"
        )
    return selected


def build_chapter_variation_contract(
    chapter_index: int,
    *,
    outline: list[str] | None = None,
    recent_patterns: list[dict[str, Any]] | None = None,
) -> str:
    """Build a concrete anti-homogenization contract for chapter generation.

    Older projects often contain only one-line chapter outlines.  In that case
    a model sees the same generic task every time and defaults to exposition.
    This contract gives it a rotating dramatic engine while preserving any
    explicit hook type written by the author.
    """
    nodes = [str(node).strip() for node in (outline or []) if str(node).strip()]
    explicit_hook = ""
    for node in nodes:
        match = re.search(r"章末钩子[（(]([^）)]+)[）)]", node)
        if match:
            explicit_hook = match.group(1).strip()
            break
    profile = _select_variation_profile(
        chapter_index,
        explicit_hook=explicit_hook,
        recent_patterns=recent_patterns,
    )
    hook = profile["hook"]
    recent = recent_patterns or []
    recent_engines = [
        str(item.get("variationEngine") or "").strip()
        for item in recent
        if str(item.get("variationEngine") or "").strip()
    ]
    recent_hooks = [
        str(item.get("actualHookType") or item.get("hookType") or "").strip()
        for item in recent
        if str(item.get("actualHookType") or item.get("hookType") or "").strip()
    ]
    avoid_lines = []
    if recent_engines:
        avoid_lines.append("近期已用叙事发动机：" + "、".join(recent_engines))
    if recent_hooks:
        avoid_lines.append("近期已用章尾钩子类型：" + "、".join(recent_hooks))
    avoid = "\n".join(avoid_lines) if avoid_lines else "近期没有可供去重的结构记录。"
    return (
        "本章必须使用下面指定的戏剧发动机，不能把同一套查账、核验、解释流程换名重写。\n"
        f"本章指定发动机：{profile['engine']}；主要冲突载体：{profile['carrier']}。\n"
        f"本章指定章尾钩子类型：{hook}。{profile['instruction']}\n"
        f"钩子落地形状：{profile['hook_shape']}\n"
        f"{avoid}\n"
        "上述发动机和钩子是本章的硬约束；近期记录中的结构禁止复用，作者在本章章纲中明确写出的事实优先但不得取消章尾钩子。\n"
        "硬验收：开头150字内发生压力；中段由可见行动使原策略失效；主角作出有代价的选择；最后120到250字只落地一个新动作或发现，"
        "并停在下一章必须回答的具体问题之前。章尾必须写出‘谁/什么对象 + 已经发生或正在发生的动作 + 尚未解决的后果’，"
        "例如门已被撞开、货车已经转向、证物落入对手手中、期限正在减少；‘准备、打算、想要、将要、跟上去看看’等计划式表达不能单独算钩子。"
        "不得用‘接下来/新的篇章/埋下伏笔/局势变化’等总结代替钩子，也不得在章尾提前解决未决问题。"
    )


def build_chapter_dramatic_blueprint(
    chapter_index: int,
    *,
    outline: list[str] | None = None,
    recent_patterns: list[dict[str, Any]] | None = None,
) -> str:
    """Return an explicit five-beat plan for the prose model.

    The old contract described good intentions but left the model to invent
    scene order.  This blueprint makes every chapter carry a state change and
    a concrete ending action while still allowing the author's outline to
    decide the actual facts.
    """
    nodes = [str(node).strip() for node in (outline or []) if str(node).strip()]
    explicit_hook = ""
    for node in nodes:
        match = re.search(r"章末钩子[（(]([^）)]+)[）)]", node)
        if match:
            explicit_hook = match.group(1).strip()
            break
    profile = _select_variation_profile(
        chapter_index,
        explicit_hook=explicit_hook,
        recent_patterns=recent_patterns,
    )
    recent = recent_patterns or []
    recent_summary = "、".join(
        f"第{item.get('chapterIndex')}章:{item.get('variationEngine') or item.get('hookType') or '未知'}"
        for item in recent[-4:]
        if isinstance(item, dict)
    ) or "无"
    # The full outline is already passed through the prompt-security boundary
    # by the generation service. Do not echo raw author text here: a chapter
    # outline can contain angle brackets or forged role markers.
    outline_anchor = "以已注入的本章章纲中的具体人物、地点和目标为事实锚点"
    return (
        "【本章五拍戏剧蓝图｜只用于写作前内部规划，不得把拍名或自评写进正文】\n"
        f"章节功能：{profile['engine']}；冲突载体：{profile['carrier']}。\n"
        f"章纲锚点：{outline_anchor}\n"
        f"近期已用结构：{recent_summary}\n"
        "1. 入场压力（前10%）：让具体人物在正在做的事上立刻遇到可见阻力，禁止先讲背景。\n"
        "2. 对手升级（约25%-45%）：有自身利益的对手主动加码，改变条件或夺走一项资源。\n"
        "3. 策略失效（约55%-70%）：用一个可观察事件击穿主角原计划，不能只靠‘却/突然’宣布变化。\n"
        "4. 选择与代价（约70%-90%）：主角亲自选择较坏的两个选项之一，立即失去关系、资源、名誉、时间或安全中的至少一项。\n"
        f"5. 章尾追读债（最后一小段）：{profile['hook_shape']} 只留下一个下一章必须回答的具体问题；不总结、不预告、不把钩子解释完。\n"
        "去重要求：不能复用近期章节的开场压力、冲突载体、破局方式、代价来源或钩子落点；即使事实相近，也要换人物关系或行动机制。\n"
        "落笔前默写五拍的具体人物/对象/动作，正文只呈现故事，不得输出‘开场压力、策略失效、钩子’等工程标签。"
    )


BASE = WritingSkill(
    id="base.novel.zh",
    version="1.0.0",
    category="base",
    priority=10,
    prompt=(
        "你是中文商业小说的协作作者。严格服从作者已确认的设定和本章章纲，不擅自改写事实、"
        "人物关系、时间线或力量规则。保持叙事视角稳定，情节以可见行动、对话和细节推进；"
        "不要写创作说明、标题、Markdown、总结或下一章预告，只输出可直接进入正文的小说文本。"
    ),
)

DRAMA = WritingSkill(
    id="craft.dramatic-motion",
    version="1.1.0",
    category="craft",
    priority=25,
    prompt=(
        "小说首先写人，不写办事报告。整章围绕一个人物此刻非赢不可的具体欲望展开；让一个有自身利益的"
        "对手主动阻拦，并至少两次升级压力。主角必须在损失、关系、名声、利益或原则之间作出选择，"
        "选择要产生不可撤销的后果。把证据、工序、账目和规则压缩为冲突所需的关键细节，禁止连续多段只做"
        "登记、核验、复称、解释权限或罗列数字。对白要有隐瞒、试探、威胁、误解或交换，不能人人都像在"
        "宣读制度。先在内部为每个场景核对‘入场状态→人物目标→阻力→策略→转折触发→选择/代价→离场状态’，"
        "其中转折必须让原策略失效，至少改变目标、风险、关系、信息、资源、身份或情绪立场之一；仅增加一条消息、"
        "换一个说法或让旁白宣布‘局势变了’都不算转折。全章中段必须发生一次可被行动证明的策略失效，高潮要"
        "兑现本章核心情绪，章末钩子来自人物刚刚作出的选择或对手的新行动，并明确留下一个下一章必须回答的"
        "具体问题。钩子可用危机、未完成动作、身份偏差、两难选择、倒计时、证据缺口或关系威胁，但不能提前"
        "解决它，也不能连续两章机械复用同一种钩子。不得用待办清单、规则复述或‘不等于’式总结收尾。"
        "熟人突然现身后只写‘他怎么会在这里’属于识人问句，不算钩子；必须让这个人当场提出条件、实施威胁、"
        "交出矛盾证物或打断主角正在进行的行动。威胁型钩子不必强行写问号，但威胁必须已经启动并让下一章承担后果。"
        "同一章不得连续安排两轮‘求见或解释→被拒→换人再解释→再次被拒’；若第二轮不可删，必须改变筹码、关系或期限。"
        "写作前在心里完成五拍节奏，不把节拍标题写进正文：开场前150字内让压力通过动作发生；前半段让对手至少"
        "升级一次；全章约55%到75%处用可见事件击穿原策略；随后让主角在两种损失之间做出不可撤销的选择并立即付出代价；"
        "最后120到250字只落地一个具体动作、发现或新威胁，停在一个能用一句话回答的未决问题前。若近期结构记录已占用"
        "某种场景机制、冲突载体或钩子类型，本章必须换成不同机制，不得只替换名词。"
    ),
)

GENRE_SKILLS = {
    "farming": WritingSkill(
        "genre.farming",
        "1.0.0",
        "genre",
        20,
        "种田/经营叙事要让资源、成本、工序、交易与生活改善形成可追踪的因果链。数字只是压力和选择的"
        "证据，重点写饥饿、尊严、家庭、信任和利益怎样被一笔交易改变。避免凭空暴富，成果必须由人物"
        "能力、时代条件和前文积累共同支撑；不要把完整操作流程当成剧情本身。",
    ),
    "romance": WritingSkill(
        "genre.romance",
        "1.0.0",
        "genre",
        20,
        "感情推进依靠选择、边界、误解的澄清和共同经历，不用强行降智制造冲突。关系变化应与"
        "人物既有性格和阶段一致，保留女主的主体性。",
    ),
    "mystery": WritingSkill(
        "genre.mystery",
        "1.0.0",
        "genre",
        20,
        "悬疑信息遵守公平揭示：线索出现时可被读者观察，结论由证据链推出；控制答案揭示速度，"
        "不靠突然新增的关键事实解谜。",
    ),
    "fantasy": WritingSkill(
        "genre.fantasy",
        "1.0.0",
        "genre",
        20,
        "力量、术法和世界规则必须遵守设定库中的限制与代价。升级和胜负要有铺垫，不临时添加能力解决困境。",
    ),
    "historical": WritingSkill(
        "genre.historical",
        "1.0.0",
        "genre",
        20,
        "时代生活、身份礼法与生产条件保持一致；现代知识只能通过角色能获得的材料和验证过程落地，"
        "避免现代术语直接出现在人物语言中。",
    ),
}

TASK_SKILLS = {
    "chapter": WritingSkill(
        "task.chapter",
        "1.1.0",
        "task",
        30,
        "完整覆盖章纲，但不要逐条照抄节点或按清单顺序机械展开。先确定本章的情绪承诺与决定性选择，"
        "再把必要节点编织进同一条冲突链。每个场景都要改变人物关系、风险、信息或资源中的至少一项；"
        "章末形成阶段性结果和新的迫切问题，但不要为了钩子突然截断动作。"
        "正文节奏必须可辨认地经过开场压力、对手升级、策略失效、代价选择和章尾未决动作五个阶段；"
        "开场压力尽早发生，策略失效位于中段而不是结尾，钩子只在最后一小段真实落地。相邻章节如果"
        "使用过相同的冲突载体、解决方式或钩子类型，本章要主动改换冲突机制和情绪落点。熟人露面加一句"
        "‘怎么会在这里’、主角离场思考下一步、或正文结束前才宣布‘出事了’，都不能替代已经启动的新威胁、"
        "带代价的选择或具体矛盾证物。",
    ),
    "continue": WritingSkill(
        "task.continue",
        "1.0.0",
        "task",
        30,
        "从给定正文最后一个动作、语气和视角自然续写，不复述前文，不另起无关场景。续写段至少完成一次"
        "目标、阻力或信息状态的可见变化；结尾落在未完成动作、具体发现、迫切决定或对手新行动上，留下一个"
        "能用一句话说清的未决问题，不用总结或空泛预告收尾。",
    ),
    "expand": WritingSkill(
        "task.expand",
        "1.0.0",
        "task",
        30,
        "保留原意和事件结果，通过动作反应、环境交互、对话潜台词补足过程；不要用同义反复凑字数。",
    ),
    "polish": WritingSkill(
        "task.polish",
        "1.0.0",
        "task",
        30,
        "不改变事实、情节顺序和人物意图，压缩空泛表述，修正病句，使节奏和意象更准确。",
    ),
    "rewrite_tone": WritingSkill(
        "task.rewrite-tone",
        "1.0.0",
        "task",
        30,
        "保持事实与信息量不变，重写表达和语气；不得借改写新增剧情或设定。",
    ),
    "author_style": WritingSkill(
        "task.author-style",
        "1.0.0",
        "task",
        30,
        "在不复制样本文句的前提下，优先匹配作者风格档中的句长、对白密度、意象与用词习惯。",
    ),
    "naturalize": WritingSkill(
        "task.naturalize",
        "1.0.0",
        "task",
        30,
        "只对作者选中的正文生成自然化候选。保留人物、时间、地点、数字、关系、资源、伏笔和事件结果，"
        "不得新增事实或替作者改变立场。优先删除模板化衔接、解释性重复和机械对仗；保留有意的口头禅、"
        "方言、断句和人物不完整表达。输出候选正文和修改理由，不输出创作说明。",
    ),
}

SCENE_SKILLS = {
    "political": WritingSkill(
        "scene.political",
        "1.0.0",
        "scene",
        40,
        "权谋不是比谁更懂手续，而是争夺人、资源、合法性与叙事权。各方都要主动落子并预判对方；每次"
        "胜负都带交换条件、隐性代价或阵营裂痕。证据可以成为武器，但不能让补齐文书自动解决危机。"
        "至少让一名盟友动摇、一个对手取得局部胜利，或迫使主角在两种损失之间选择。",
    ),
    "relationship": WritingSkill(
        "scene.relationship",
        "1.0.0",
        "scene",
        40,
        "关系场景用距离、停顿、回避、试探和具体选择表达变化，少用旁白直接宣布感情。",
    ),
    "negotiation": WritingSkill(
        "scene.negotiation",
        "1.0.0",
        "scene",
        40,
        "谈判双方都要有筹码、底线和信息差；每轮对话改变条件或判断，结果对应前文建立的利益结构。",
    ),
    "combat": WritingSkill(
        "scene.combat",
        "1.0.0",
        "scene",
        40,
        "战斗保持空间位置、体力、伤势和能力限制连续；动作产生结果，避免招式清单式描写。",
    ),
    "business": WritingSkill(
        "scene.business",
        "1.0.0",
        "scene",
        40,
        "经营场景明确商品、客群、成本、产能和风险，让决策通过实际反馈验证。",
    ),
    "investigation": WritingSkill(
        "scene.investigation",
        "1.0.0",
        "scene",
        40,
        "调查按观察、假设、验证推进，区分角色已知与读者已知，不让角色无依据猜中答案。",
    ),
    "general": WritingSkill(
        "scene.general",
        "1.0.0",
        "scene",
        40,
        "场景必须有人物目标、阻力和结果；描写服务于行动与情绪变化。",
    ),
}


def _genre_keys(genre: str) -> list[str]:
    value = genre.lower()
    matches: list[str] = []
    rules = (
        ("farming", ("种田", "经营", "经商", "美食")),
        ("romance", ("女频", "言情", "爱情", "甜宠", "婚恋")),
        ("mystery", ("悬疑", "推理", "探案")),
        ("fantasy", ("玄幻", "仙侠", "修真", "奇幻")),
        ("historical", ("古代", "历史", "穿越", "架空")),
    )
    for key, markers in rules:
        if any(marker in value for marker in markers):
            matches.append(key)
    return matches


def _scene_key(text: str) -> str:
    rules = (
        (
            "political",
            ("权谋", "派系", "朝堂", "京中", "官府", "县衙", "保护伞", "官场", "夺权"),
        ),
        ("combat", ("战", "打斗", "追杀", "刺杀", "交锋")),
        ("investigation", ("调查", "查案", "线索", "真相", "追查")),
        ("negotiation", ("谈判", "议价", "条件", "合作", "讨价")),
        ("business", ("经营", "生意", "买卖", "作坊", "铺子", "种田", "收成")),
        ("relationship", ("感情", "心意", "误会", "成亲", "关系", "告白")),
    )
    for key, markers in rules:
        if any(marker in text for marker in markers):
            return key
    return "general"


def select_writing_skills(*, genre: str | None, task: str, outline: list[str], instruction: str = "") -> SkillSelection:
    """Select a stable ordered skill set from explicit story inputs."""
    selected = [BASE]
    selected.extend(GENRE_SKILLS[key] for key in _genre_keys(genre or ""))
    if task == "chapter":
        selected.append(DRAMA)
    selected.append(TASK_SKILLS.get(task, TASK_SKILLS["chapter"]))
    scene = _scene_key("\n".join(outline) + "\n" + instruction)
    selected.append(SCENE_SKILLS[scene])
    selected.sort(key=lambda skill: (skill.priority, skill.id))
    return SkillSelection(tuple(selected), scene)
