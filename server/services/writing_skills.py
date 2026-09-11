"""Deterministic runtime writing-skill selection for novel generation.

These skills are application prompt modules, not Codex ``SKILL.md`` files.  Keeping
them as versioned data makes selection auditable and lets tests detect accidental
prompt drift.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    version="1.0.0",
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
        "1.0.0",
        "task",
        30,
        "完整覆盖章纲，但不要逐条照抄节点或按清单顺序机械展开。先确定本章的情绪承诺与决定性选择，"
        "再把必要节点编织进同一条冲突链。每个场景都要改变人物关系、风险、信息或资源中的至少一项；"
        "章末形成阶段性结果和新的迫切问题，但不要为了钩子突然截断动作。",
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
