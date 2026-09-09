<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

const menuOpen = ref(false)
const scrolled = ref(false)
const githubUrl = 'https://github.com/huihuijiangqiang/moshu'

function syncScroll() {
  scrolled.value = window.scrollY > 16
}

onMounted(() => {
  syncScroll()
  window.addEventListener('scroll', syncScroll, { passive: true })
})
onUnmounted(() => window.removeEventListener('scroll', syncScroll))
</script>

<template>
  <main class="landing">
    <header class="landing-nav" :data-scrolled="scrolled" :data-open="menuOpen">
      <RouterLink class="brand" to="/" aria-label="墨枢首页" @click="menuOpen = false">
        <span class="brand-mark">墨</span>
        <span class="brand-copy"><strong>墨枢</strong><small>MOSHU</small></span>
      </RouterLink>
      <button class="menu-toggle" type="button" :aria-expanded="menuOpen" aria-label="打开导航" @click="menuOpen = !menuOpen">
        <span /><span />
      </button>
      <nav class="landing-menu" aria-label="首页导航">
        <a href="#workflow" @click="menuOpen = false">创作流程</a>
        <a href="#capabilities" @click="menuOpen = false">核心能力</a>
        <a href="#adaptation" @click="menuOpen = false">漫剧分镜</a>
        <a :href="githubUrl" target="_blank" rel="noreferrer">GitHub <span aria-hidden="true">↗</span></a>
        <RouterLink class="nav-workspace" to="/workspace" @click="menuOpen = false">进入工作台 <span aria-hidden="true">→</span></RouterLink>
      </nav>
    </header>

    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-rule"><span>AI NOVEL STUDIO</span><span>LONG-FORM / CONSISTENCY / CODEX</span></div>
      <div class="hero-copy">
        <p class="eyebrow"><span /> 面向长篇创作的 AI 工作台</p>
        <h1 id="hero-title">墨枢 AI 长篇小说<br>创作工作台</h1>
        <p class="hero-lead">把灵感、大纲、人物设定和数十万字正文放进同一条创作脉络。AI 负责续写、检索与核对，你始终掌握故事方向。</p>
        <div class="hero-actions">
          <RouterLink class="button button-primary" to="/workspace">进入工作台 <span aria-hidden="true">→</span></RouterLink>
          <a class="button button-quiet" :href="githubUrl" target="_blank" rel="noreferrer">查看源代码 <span aria-hidden="true">↗</span></a>
        </div>
      </div>
      <div class="hero-window" aria-label="墨枢写作台产品界面预览">
        <div class="window-bar"><span /><span /><span /><b>剑起山河 · 第 87 章</b><small>内容已保存</small></div>
        <img src="/landing/writer.png" alt="墨枢写作台界面，包含章节、正文、AI 辅助与一致性提醒" width="1440" height="900">
        <div class="glass-note glass-note-context"><small>本章上下文</small><strong>21.4k / 25k</strong><i><b /></i></div>
        <div class="glass-note glass-note-guard"><small>一致性守卫</small><strong>5 条待处理</strong></div>
      </div>
      <div class="hero-foot">
        <span>开源可部署</span><span>本地优先</span><span>模型服务可配置</span><a href="#workflow">了解创作流程 ↓</a>
      </div>
    </section>

    <section id="workflow" class="workflow" aria-labelledby="workflow-title">
      <div class="section-heading">
        <p class="eyebrow"><span /> 从一句话到完整作品</p>
        <h2 id="workflow-title">一条不断回流的创作链路</h2>
        <p>大纲和设定不是写作前的一次性表格。每次正文变化，都能回到设定库、时间线与一致性检查中继续演化。</p>
      </div>
      <ol class="workflow-track">
        <li><span>01</span><strong>灵感</strong><small>确定题材与核心冲突</small></li>
        <li><span>02</span><strong>大纲</strong><small>拆解卷、章与情节拍</small></li>
        <li><span>03</span><strong>设定</strong><small>沉淀人物和世界规则</small></li>
        <li><span>04</span><strong>写作</strong><small>续写、扩写与风格控制</small></li>
        <li><span>05</span><strong>守卫</strong><small>核对事实、时间与状态</small></li>
        <li><span>06</span><strong>导出</strong><small>整理作品与创作凭证</small></li>
      </ol>
    </section>

    <section id="capabilities" class="capability-band" aria-labelledby="context-title">
      <div class="capability-copy">
        <p class="eyebrow eyebrow-light"><span /> 长文本上下文</p>
        <h2 id="context-title">不只记得上一章，<br>还要理解整部小说。</h2>
        <p>按当前写作任务弹性组织大纲、相邻正文、人物状态和相关伏笔。长上下文与语义检索协同，避免把宝贵窗口浪费在无关内容上。</p>
        <dl class="signal-list">
          <div><dt>弹性预算</dt><dd>根据任务与篇幅动态分配</dd></div>
          <div><dt>相关检索</dt><dd>召回人物、地点、物品与伏笔</dd></div>
          <div><dt>注入防护</dt><dd>隔离资料内容与系统指令</dd></div>
        </dl>
      </div>
      <div class="context-console" aria-label="上下文构成示意">
        <div class="console-head"><span>CONTEXT ASSEMBLY</span><span>48.2K TOKENS</span></div>
        <div class="context-row context-row-strong"><span>当前章节与任务</span><b>14.8K</b><i style="--share: 74%" /></div>
        <div class="context-row"><span>相邻正文</span><b>10.2K</b><i style="--share: 51%" /></div>
        <div class="context-row"><span>人物与世界设定</span><b>8.6K</b><i style="--share: 43%" /></div>
        <div class="context-row"><span>相关伏笔与时间线</span><b>7.9K</b><i style="--share: 39%" /></div>
        <div class="context-row"><span>风格样本与写作约束</span><b>6.7K</b><i style="--share: 33%" /></div>
        <div class="console-foot"><span>预算仍可扩展</span><strong>256K WINDOW</strong></div>
      </div>
    </section>

    <section class="product-section" aria-labelledby="codex-title">
      <div class="product-visual">
        <span class="visual-index">01 / CODEX</span>
        <img src="/landing/codex.png" alt="墨枢设定库界面，展示人物档案与写作约束" width="1440" height="900" loading="lazy">
      </div>
      <div class="product-copy">
        <p class="eyebrow"><span /> Story Bible</p>
        <h2 id="codex-title">设定跟着故事生长</h2>
        <p>人物、势力、地点、物品与力量体系不再散落在文档里。正文产生的新事实进入待确认区，经作者审核后成为后续创作的可靠依据。</p>
        <ul>
          <li><strong>动态状态</strong><span>记录人物在具体章节的身份、能力和关系变化</span></li>
          <li><strong>常驻上下文</strong><span>把不可违背的规则稳定带入每次生成</span></li>
          <li><strong>可追溯来源</strong><span>从设定回看对应章节，而不是相信孤立摘要</span></li>
        </ul>
      </div>
    </section>

    <section class="product-section product-section-reverse" aria-labelledby="guard-title">
      <div class="product-visual">
        <span class="visual-index">02 / GUARD</span>
        <img src="/landing/guard.png" alt="墨枢一致性守卫界面，展示设定冲突与证据对照" width="1440" height="900" loading="lazy">
      </div>
      <div class="product-copy">
        <p class="eyebrow"><span /> Consistency Guard</p>
        <h2 id="guard-title">冲突有证据，修改有选择</h2>
        <p>写到第八十七章，仍能发现第四十一章留下的兵器状态、人物能力和时间顺序冲突。每条提醒给出前后文证据，由作者决定改正文、更新设定或忽略。</p>
        <div class="guard-stats">
          <div><strong>事实</strong><span>人物与物品状态</span></div>
          <div><strong>时序</strong><span>事件先后与间隔</span></div>
          <div><strong>承诺</strong><span>伏笔、目标与未解线索</span></div>
        </div>
      </div>
    </section>

    <section id="adaptation" class="adaptation" aria-labelledby="adaptation-title">
      <div class="adaptation-head">
        <div><p class="eyebrow"><span /> 从文字到镜头</p><h2 id="adaptation-title">为漫剧改编预先整理视觉叙事</h2></div>
        <p>基于人物与场景设定拆分镜头、对白、动作和画面提示，先把故事变成可审阅的分镜脚本，再决定是否进入图像与视频生产。</p>
      </div>
      <div class="storyboard-strip" aria-label="漫剧分镜能力示例">
        <article><span>SCENE 12</span><div class="frame frame-one"><b>远景</b><i>雪夜 · 城门</i></div><p>军队停在结冰的护城河外，火把沿城墙延伸。</p></article>
        <article><span>SHOT 03</span><div class="frame frame-two"><b>近景</b><i>主角 · 沈砚</i></div><p>他握住断刃，左肩旧伤渗血，视线越过城垛。</p></article>
        <article><span>DIALOGUE</span><div class="frame frame-three"><b>对白</b><i>压低声音</i></div><p>“今夜不开城门。先查清那封信是谁送来的。”</p></article>
      </div>
    </section>

    <section class="final-cta" aria-labelledby="cta-title">
      <span class="cta-number">墨 / 01</span>
      <div><p class="eyebrow eyebrow-light"><span /> READY TO WRITE</p><h2 id="cta-title">让长篇创作真正连贯起来。</h2><p>在线预览使用浏览器本地数据，可以直接体验完整写作流程。</p></div>
      <div class="cta-actions">
        <RouterLink class="button button-gold" to="/workspace">进入工作台 <span aria-hidden="true">→</span></RouterLink>
        <a class="text-link" :href="githubUrl" target="_blank" rel="noreferrer">在 GitHub 查看项目 ↗</a>
      </div>
    </section>

    <footer class="landing-footer">
      <RouterLink class="footer-brand" to="/"><span>墨</span><strong>墨枢 MOSHU</strong></RouterLink>
      <p>AI 长篇小说创作工作台</p>
      <nav aria-label="页脚导航"><RouterLink to="/workspace">工作台</RouterLink><a :href="githubUrl" target="_blank" rel="noreferrer">GitHub</a><a href="#capabilities">核心能力</a></nav>
      <small>© {{ new Date().getFullYear() }} Moshu. Open source on GitHub.</small>
    </footer>
  </main>
</template>

<style scoped>
.landing {
  --land-ink: #17211f;
  --land-teal: #163b3a;
  --land-deep: #0e2928;
  --land-cloud: #f4f7f6;
  --land-gold: #d3a84b;
  --land-line: rgba(23, 33, 31, 0.15);
  min-height: 100vh;
  overflow: hidden;
  color: var(--land-ink);
  background: var(--land-cloud);
  font-family: Inter, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.landing :where(a) { color: inherit; text-decoration: none; }
.landing :where(h1, h2, p) { margin-top: 0; }
.landing :where(button, a) { letter-spacing: 0; }
.landing-nav {
  position: fixed; z-index: 30; top: 14px; left: 50%;
  width: min(1180px, calc(100% - 32px)); height: 64px;
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 12px 0 16px; border: 1px solid rgba(255,255,255,.75); border-radius: 8px;
  background: rgba(255,255,255,.72); box-shadow: 0 12px 34px rgba(25,54,51,.08);
  backdrop-filter: blur(18px) saturate(125%); transform: translateX(-50%);
}
.landing-nav[data-scrolled="true"] { background: rgba(255,255,255,.92); box-shadow: 0 14px 40px rgba(14,41,40,.14); }
.brand { display: flex; align-items: center; gap: 10px; min-width: 130px; }
.brand-mark { width: 36px; height: 36px; display: grid; place-items: center; border-radius: 4px; color: #fff; background: var(--land-teal); font: 700 20px/1 "Songti SC", SimSun, serif; }
.brand-copy { display: grid; line-height: 1; }
.brand-copy strong { font-size: 16px; }
.brand-copy small { margin-top: 5px; color: #697674; font-size: 8px; letter-spacing: .18em; }
.landing-menu { display: flex; align-items: center; gap: 28px; font-size: 13px; font-weight: 600; }
.nav-workspace { height: 40px; display: inline-flex; align-items: center; gap: 16px; padding: 0 16px; border-radius: 4px; color: #fff !important; background: var(--land-teal); }
.menu-toggle { display: none; }
.hero { position: relative; min-height: 980px; padding: 126px max(28px, calc((100vw - 1180px)/2)) 46px; background: #eef3f1; }
.hero::before { content: ""; position: absolute; top: 0; right: max(28px, calc((100vw - 1180px)/2)); width: 31%; height: 460px; border-inline: 1px solid rgba(22,59,58,.12); background: rgba(255,255,255,.2); }
.hero-rule { position: relative; z-index: 1; display: flex; justify-content: space-between; padding-bottom: 16px; border-bottom: 1px solid var(--land-line); color: #6e7c79; font: 10px/1 ui-monospace, Consolas, monospace; letter-spacing: .12em; }
.hero-copy { position: relative; z-index: 2; max-width: 820px; padding: 58px 0 54px; }
.eyebrow { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; color: #49615e; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.eyebrow span { width: 30px; height: 1px; background: var(--land-gold); }
.hero h1 { margin-bottom: 24px; color: var(--land-deep); font: 700 clamp(48px,6.2vw,82px)/1.12 "Songti SC","STSong",SimSun,serif; letter-spacing: 0; }
.hero-lead { max-width: 660px; margin-bottom: 30px; color: #536461; font: 18px/1.9 "Songti SC","STSong",SimSun,serif; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 12px; }
.button { min-height: 48px; display: inline-flex; align-items: center; justify-content: center; gap: 24px; padding: 0 20px; border: 1px solid transparent; border-radius: 4px; font-size: 14px; font-weight: 700; transition: transform 180ms ease, background-color 180ms ease; }
.button:hover { transform: translateY(-2px); }
.button-primary { color: #fff !important; background: var(--land-teal); }
.button-quiet { border-color: rgba(22,59,58,.24); background: rgba(255,255,255,.4); }
.hero-window { position: relative; z-index: 4; width: min(1120px,100%); margin-left: auto; border: 1px solid rgba(14,41,40,.2); border-radius: 8px; background: rgba(255,255,255,.56); box-shadow: 0 38px 90px rgba(14,41,40,.2); backdrop-filter: blur(18px); }
.hero-window img { display: block; width: 100%; height: auto; border-radius: 0 0 7px 7px; }
.window-bar { height: 42px; display: flex; align-items: center; gap: 7px; padding: 0 14px; border-bottom: 1px solid rgba(14,41,40,.14); }
.window-bar > span { width: 8px; height: 8px; border-radius: 50%; background: #c1cbc8; }
.window-bar b { margin-left: 10px; color: #536461; font-size: 11px; }
.window-bar small { margin-left: auto; color: #72807e; font-size: 10px; }
.glass-note { position: absolute; z-index: 5; min-width: 166px; padding: 13px 15px; border: 1px solid rgba(255,255,255,.8); border-radius: 6px; background: rgba(248,251,250,.82); box-shadow: 0 18px 44px rgba(14,41,40,.18); backdrop-filter: blur(15px); }
.glass-note small,.glass-note strong { display: block; }
.glass-note small { margin-bottom: 4px; color: #63726f; font-size: 10px; }
.glass-note strong { color: var(--land-teal); font: 700 13px/1.4 ui-monospace,Consolas,monospace; }
.glass-note-context { top: 92px; right: -34px; }
.glass-note-context > i { height: 3px; display: block; margin-top: 10px; background: #d8dfdd; }
.glass-note-context > i b { width: 72%; height: 100%; display: block; background: var(--land-gold); }
.glass-note-guard { right: 30px; bottom: -24px; border-left: 3px solid #b44d56; }
.hero-foot { position: relative; z-index: 5; display: flex; gap: 28px; margin-top: 46px; color: #687673; font-size: 11px; }
.hero-foot span::before { content: "+"; margin-right: 8px; color: var(--land-gold); }
.hero-foot a { margin-left: auto; color: var(--land-teal); font-weight: 700; }
.workflow { padding: 112px max(28px,calc((100vw - 1180px)/2)) 124px; background: #fff; }
.section-heading { display: grid; grid-template-columns: 1.1fr 1fr; gap: 18px 80px; align-items: end; }
.section-heading .eyebrow { grid-column: 1/-1; margin: 0; }
.section-heading h2,.product-copy h2,.adaptation h2,.final-cta h2 { margin-bottom: 0; font: 700 clamp(34px,4vw,52px)/1.25 "Songti SC","STSong",SimSun,serif; letter-spacing: 0; }
.section-heading > p:last-child { margin: 0; color: #667572; font-size: 15px; line-height: 1.9; }
.workflow-track { display: grid; grid-template-columns: repeat(6,1fr); margin: 70px 0 0; padding: 0; border-top: 1px solid var(--land-line); list-style: none; }
.workflow-track li { min-width: 0; padding: 22px 14px 0; border-right: 1px solid var(--land-line); }
.workflow-track span { display: block; margin-bottom: 34px; color: #8a9694; font: 11px/1 ui-monospace,Consolas,monospace; }
.workflow-track strong { display: block; margin-bottom: 8px; color: var(--land-teal); font: 700 24px/1.3 "Songti SC",SimSun,serif; }
.workflow-track small { color: #75817f; font-size: 11px; line-height: 1.6; }
.capability-band { display: grid; grid-template-columns: .85fr 1.15fr; gap: 100px; padding: 112px max(28px,calc((100vw - 1180px)/2)); color: #fff; background: var(--land-deep); }
.eyebrow-light { color: #b8c8c5; }
.capability-copy h2 { margin-bottom: 26px; color: #fff; font: 700 clamp(36px,4.4vw,58px)/1.3 "Songti SC","STSong",SimSun,serif; }
.capability-copy > p:not(.eyebrow) { margin-bottom: 38px; color: #b8c8c5; font-size: 15px; line-height: 1.9; }
.signal-list { margin: 0; border-top: 1px solid rgba(255,255,255,.16); }
.signal-list > div { display: grid; grid-template-columns: 110px 1fr; gap: 20px; padding: 14px 0; border-bottom: 1px solid rgba(255,255,255,.16); }
.signal-list dt { color: #fff; font-weight: 700; }.signal-list dd { margin: 0; color: #9eb2ae; }
.context-console { align-self: center; padding: 24px; border: 1px solid rgba(255,255,255,.16); border-radius: 8px; background: rgba(255,255,255,.07); box-shadow: 0 30px 80px rgba(0,0,0,.22); backdrop-filter: blur(14px); }
.console-head,.console-foot { display: flex; justify-content: space-between; color: #8ca5a0; font: 10px/1 ui-monospace,Consolas,monospace; letter-spacing: .1em; }
.console-head { padding-bottom: 18px; border-bottom: 1px solid rgba(255,255,255,.16); }
.context-row { display: grid; grid-template-columns: 1fr auto; gap: 8px 20px; padding: 17px 0 4px; color: #bfd0cc; font-size: 12px; }
.context-row b { color: #d9e5e2; font: 11px/1 ui-monospace,Consolas,monospace; }
.context-row i { grid-column: 1/-1; height: 3px; background: rgba(255,255,255,.11); }
.context-row i::before { content: ""; width: var(--share); height: 100%; display: block; background: #6f9691; }
.context-row-strong { color: #fff; }.context-row-strong i::before { background: var(--land-gold); }
.console-foot { margin-top: 22px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,.16); }.console-foot strong { color: var(--land-gold); }
.product-section { display: grid; grid-template-columns: minmax(0,1.45fr) minmax(320px,.55fr); gap: 84px; align-items: center; padding: 120px max(28px,calc((100vw - 1320px)/2)); background: #edf2f0; }
.product-section-reverse { grid-template-columns: minmax(320px,.55fr) minmax(0,1.45fr); background: #fff; }
.product-section-reverse .product-visual { grid-column: 2; }.product-section-reverse .product-copy { grid-column: 1; grid-row: 1; }
.product-visual { position: relative; padding-top: 36px; }
.product-visual::before { content: ""; position: absolute; inset: 0 28px 18px 0; border: 1px solid rgba(22,59,58,.14); border-radius: 8px; background: rgba(255,255,255,.54); }
.product-visual img { position: relative; z-index: 1; width: 100%; display: block; border: 1px solid rgba(22,59,58,.2); border-radius: 6px; box-shadow: 0 30px 70px rgba(25,54,51,.16); }
.visual-index { position: absolute; z-index: 2; top: 0; color: #6d7a78; font: 10px/1 ui-monospace,Consolas,monospace; letter-spacing: .12em; }
.product-copy h2 { margin-bottom: 24px; color: var(--land-deep); }
.product-copy > p:not(.eyebrow) { color: #60706d; font-size: 15px; line-height: 1.9; }
.product-copy ul { margin: 32px 0 0; padding: 0; border-top: 1px solid var(--land-line); list-style: none; }
.product-copy li { display: grid; gap: 4px; padding: 14px 0; border-bottom: 1px solid var(--land-line); }
.product-copy li span { color: #72807e; font-size: 12px; line-height: 1.6; }
.guard-stats { display: grid; grid-template-columns: repeat(3,1fr); margin-top: 38px; border-top: 1px solid var(--land-line); }
.guard-stats div { padding: 18px 12px 0 0; border-right: 1px solid var(--land-line); }
.guard-stats strong,.guard-stats span { display: block; }.guard-stats strong { color: var(--land-teal); font: 700 22px/1.3 "Songti SC",SimSun,serif; }.guard-stats span { margin-top: 6px; color: #75817f; font-size: 10px; }
.adaptation { padding: 120px max(28px,calc((100vw - 1180px)/2)); background: #e4ebe8; }
.adaptation-head { display: grid; grid-template-columns: 1.25fr .75fr; gap: 90px; align-items: end; }
.adaptation-head > p { margin-bottom: 4px; color: #62716f; line-height: 1.9; }
.storyboard-strip { display: grid; grid-template-columns: repeat(3,1fr); gap: 1px; margin-top: 66px; border: 1px solid rgba(22,59,58,.15); background: rgba(22,59,58,.15); }
.storyboard-strip article { min-width: 0; padding: 18px; background: rgba(255,255,255,.64); }
.storyboard-strip article > span { color: #71817e; font: 9px/1 ui-monospace,Consolas,monospace; letter-spacing: .12em; }
.storyboard-strip article > p { min-height: 58px; margin: 18px 0 0; color: #4f605d; font: 14px/1.8 "Songti SC",SimSun,serif; }
.frame { position: relative; aspect-ratio: 16/9; display: flex; flex-direction: column; justify-content: flex-end; margin-top: 14px; padding: 18px; overflow: hidden; color: #fff; background: #183c3a; }
.frame::before { content: ""; position: absolute; inset: 14% 8%; border: 1px solid rgba(255,255,255,.2); }
.frame-two { background: #5d5f58; }.frame-three { color: #17211f; background: #d6b96f; }
.frame b,.frame i { position: relative; z-index: 1; }.frame b { font: 700 24px/1.3 "Songti SC",SimSun,serif; }.frame i { font-size: 10px; font-style: normal; opacity: .72; }
.final-cta { display: grid; grid-template-columns: 120px 1fr auto; gap: 50px; align-items: center; padding: 96px max(28px,calc((100vw - 1180px)/2)); color: #fff; background: var(--land-teal); }
.cta-number { align-self: stretch; padding-top: 4px; border-right: 1px solid rgba(255,255,255,.24); color: var(--land-gold); font: 11px/1 ui-monospace,Consolas,monospace; }
.final-cta h2 { color: #fff; }.final-cta p:not(.eyebrow) { margin: 15px 0 0; color: #b9cac6; }
.cta-actions { display: flex; flex-direction: column; align-items: flex-start; gap: 18px; }
.button-gold { color: #17211f !important; background: var(--land-gold); }
.text-link { padding-bottom: 3px; border-bottom: 1px solid rgba(255,255,255,.4); color: #fff !important; font-size: 12px; }
.landing-footer { display: grid; grid-template-columns: 1fr auto; gap: 22px; align-items: center; padding: 48px max(28px,calc((100vw - 1180px)/2)); color: #70807d; background: #f7f9f8; font-size: 11px; }
.footer-brand { display: flex; align-items: center; gap: 10px; color: var(--land-ink) !important; }
.footer-brand span { width: 30px; height: 30px; display: grid; place-items: center; border: 1px solid var(--land-teal); font: 16px/1 "Songti SC",SimSun,serif; }
.landing-footer p,.landing-footer small { margin: 0; }.landing-footer nav { display: flex; gap: 24px; color: var(--land-ink); }.landing-footer small { text-align: right; }
@media (max-width:980px) {
  .landing-menu { gap: 18px; }.landing-menu > a:not(.nav-workspace) { display: none; }
  .hero { min-height: auto; padding-top: 112px; }.hero-copy { padding: 46px 0; }.glass-note-context { right: 14px; }
  .capability-band,.product-section,.product-section-reverse { grid-template-columns: 1fr; gap: 60px; }
  .product-section-reverse .product-visual,.product-section-reverse .product-copy { grid-column: 1; grid-row: auto; }.product-section-reverse .product-visual { order: 1; }.product-section-reverse .product-copy { order: 2; }
  .final-cta { grid-template-columns: 70px 1fr; }.cta-actions { grid-column: 2; flex-direction: row; align-items: center; }
}
@media (max-width:720px) {
  .landing-nav { top: 8px; width: calc(100% - 16px); height: 58px; }
  .menu-toggle { width: 38px; height: 38px; display: grid; place-content: center; gap: 6px; padding: 0; border: 0; background: transparent; }
  .menu-toggle span { width: 18px; height: 1px; background: var(--land-ink); }
  .landing-menu { position: absolute; top: 65px; right: 0; left: 0; display: none; align-items: stretch; padding: 12px; border: 1px solid rgba(255,255,255,.75); border-radius: 8px; background: rgba(255,255,255,.94); box-shadow: 0 18px 44px rgba(14,41,40,.16); backdrop-filter: blur(18px); }
  .landing-nav[data-open="true"] .landing-menu { display: grid; }.landing-menu > a:not(.nav-workspace) { min-height: 42px; display: flex; align-items: center; padding: 0 10px; border-bottom: 1px solid var(--land-line); }.nav-workspace { justify-content: space-between; margin-top: 6px; }
  .hero { padding: 94px 18px 34px; }.hero::before { right: 18px; width: 42%; height: 380px; }.hero-rule span:last-child { display: none; }
  .hero-copy { padding: 38px 0 42px; }.hero h1 { font-size: clamp(40px,12vw,58px); }.hero-lead { font-size: 16px; }
  .hero-window { width: calc(100% + 42px); margin-left: -21px; border-radius: 0; }.hero-window img { width: 100%; height: auto; min-height: 0; object-fit: contain; object-position: center; border-radius: 0; }
  .glass-note { min-width: 140px; padding: 10px 12px; }.glass-note-context { top: 66px; right: 8px; }.glass-note-guard { right: 8px; bottom: -20px; }
  .hero-foot { flex-wrap: wrap; gap: 9px 18px; margin-top: 40px; }.hero-foot a { width: 100%; margin: 10px 0 0; }
  .workflow,.capability-band,.product-section,.adaptation { padding: 80px 20px; }
  .section-heading,.adaptation-head { grid-template-columns: 1fr; gap: 22px; }.section-heading .eyebrow { grid-column: 1; }
  .workflow-track { grid-template-columns: repeat(3,1fr); row-gap: 34px; }.workflow-track li:nth-child(3n) { border-right: 0; }.workflow-track span { margin-bottom: 18px; }
  .context-console { padding: 18px; }.product-section { gap: 44px; }.product-visual::before { right: 14px; bottom: 10px; }
  .storyboard-strip { grid-template-columns: 86% 86% 86%; overflow-x: auto; scroll-snap-type: x mandatory; }.storyboard-strip article { scroll-snap-align: start; }
  .final-cta { grid-template-columns: 1fr; gap: 28px; padding: 72px 20px; }.cta-number { padding: 0 0 18px; border-right: 0; border-bottom: 1px solid rgba(255,255,255,.24); }.cta-actions { grid-column: 1; flex-direction: column; align-items: stretch; }
  .landing-footer { grid-template-columns: 1fr; padding: 40px 20px; }.landing-footer nav { flex-wrap: wrap; }.landing-footer small { text-align: left; }
}
@media (prefers-reduced-motion:reduce) { .landing * { transition: none !important; } }
</style>
