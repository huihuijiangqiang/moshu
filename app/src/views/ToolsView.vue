<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useProjectNavigation } from '@/composables/use-project-navigation'
import AppIcon from '@/components/ui/AppIcon.vue'
import { request } from '@/api/http'

type ToolTab = 'names' | 'map'
type NameStyle = '古典' | '清冷' | '明快' | '异域'
type Terrain = '山河' | '群岛' | '荒原'

const { projectId } = useProjectNavigation()
const tab = ref<ToolTab>('names')
const nameStyle = ref<NameStyle>('古典')
const nameGender = ref<'女' | '男' | '不限'>('不限')
const nameCount = ref(6)
const nameSeed = ref('春山')
const names = ref<string[]>([])
const copied = ref('')
const terrain = ref<Terrain>('山河')
const regionCount = ref(7)
const mapSeed = ref('柳溪')
const mapVersion = ref(0)
const mapSaved = ref(false)
const generatedRegions = ref<Array<{ name: string; x: number; y: number }> | null>(null)

const familyNames = ['许', '沈', '顾', '周', '陆', '谢', '裴', '苏', '林', '秦', '程', '姜']
const femaleGiven = ['知微', '照棠', '明昭', '云岫', '青禾', '令仪', '晚晴', '栖月', '南枝', '见山', '初霁', '绾宁']
const maleGiven = ['砚川', '长庚', '景行', '怀瑾', '承安', '闻舟', '修远', '既白', '庭深', '昭野', '观澜', '行之']
const neutralGiven = ['知微', '砚川', '清和', '照野', '明川', '长宁', '栖迟', '怀远', '青衡', '听澜', '山止', '云开']
const exoticFamily = ['阿', '伊', '洛', '赫', '塔', '乌', '赛', '迦']
const exoticGiven = ['弥娅', '岚歌', '萨恩', '诺娅', '迦南', '维洛', '星遥', '阿岚']

const mapRegions = computed(() => {
  if (generatedRegions.value) return generatedRegions.value
  const result = []
  const count = Math.max(4, Math.min(10, regionCount.value))
  for (let i = 0; i < count; i += 1) {
    const angle = (i / count) * Math.PI * 2
    const wobble = ((mapSeed.value.charCodeAt(i % Math.max(1, mapSeed.value.length)) || 7) % 17) - 8
    result.push({
      x: 50 + Math.cos(angle) * (28 + wobble / 2),
      y: 48 + Math.sin(angle) * (25 + wobble / 3),
      name: ['柳溪', '青溪县', '白沙渡', '南岭', '望潮港', '鹤鸣原', '长风关', '照雪城', '镜湖', '栖霞镇'][i],
    })
  }
  return result
})

function seededIndex(seed: string, index: number, length: number) {
  let value = 0
  for (const char of `${seed}-${index}`) value = (value * 31 + char.charCodeAt(0)) % 1000003
  return value % length
}

async function generateNames() {
  const given = nameStyle.value === '异域'
    ? exoticGiven
    : nameGender.value === '女' ? femaleGiven : nameGender.value === '男' ? maleGiven : neutralGiven
  const surnames = nameStyle.value === '异域' ? exoticFamily : familyNames
  const localNames = Array.from({ length: nameCount.value }, (_, index) => {
    const surname = surnames[seededIndex(nameSeed.value, index, surnames.length)]
    const first = given[seededIndex(nameSeed.value, index + 17, given.length)]
    return `${surname}${first}`
  }).filter((value, index, list) => list.indexOf(value) === index)
  try {
    const response = await request<{ names: string[] }>(`/projects/${projectId.value}/tools/names`, {
      method: 'POST', body: JSON.stringify({ style: nameStyle.value, gender: nameGender.value, seed: nameSeed.value || '春山', count: nameCount.value })
    })
    names.value = response.names
  } catch {
    names.value = localNames
  }
}

async function copyName(name: string) {
  await navigator.clipboard?.writeText(name)
  copied.value = name
  window.setTimeout(() => { if (copied.value === name) copied.value = '' }, 1200)
}

async function regenerateMap() {
  mapVersion.value += 1
  mapSaved.value = false
  try {
    const draft = await request<{ regions: Array<{ name: string; x: number; y: number }> }>(`/projects/${projectId.value}/tools/maps`, {
      method: 'POST',
      body: JSON.stringify({ seed: mapSeed.value || '柳溪', terrain: terrain.value, region_count: regionCount.value }),
    })
    generatedRegions.value = draft.regions
  } catch {
    generatedRegions.value = null
  }
}

async function saveToCodex() {
  const payload = {
    seed: mapSeed.value,
    terrain: terrain.value,
    regions: mapRegions.value,
  }
  localStorage.setItem(`moshu-map-draft:${projectId.value}`, JSON.stringify(payload))
  try {
    await request(`/projects/${projectId.value}/tools/map-draft`, { method: 'PUT', body: JSON.stringify(payload) })
  } catch {
    // 本地草稿仍然保留，网络恢复后可再次保存。
  }
  mapSaved.value = true
  window.dispatchEvent(new CustomEvent('moshu:tool-codex-draft', {
    detail: { projectId: projectId.value, kind: 'location', name: `${mapSeed.value}地图草图`, terrain: terrain.value, regions: mapRegions.value },
  }))
}

generateNames()

onMounted(async () => {
  try {
    const draft = await request<{ seed: string; terrain: Terrain; regions: Array<{ name: string; x: number; y: number }> }>(`/projects/${projectId.value}/tools/map-draft`)
    mapSeed.value = draft.seed
    terrain.value = draft.terrain
    regionCount.value = draft.regions.length
    generatedRegions.value = draft.regions
  } catch {
    // 默认草图可直接使用。
  }
})
</script>

<template>
  <section class="tools-page">
    <header class="tools-header">
      <div>
        <p class="kicker">PROJECT TOOLS / {{ projectId }}</p>
        <h1>写作工具箱</h1>
        <p class="tools-subtitle">把灵感快速变成可继续使用的素材，不打断正文节奏。</p>
      </div>
      <div class="tools-stamp"><span>本地生成</span><b>不消耗额度</b></div>
    </header>

    <nav class="tools-tabs" aria-label="工具分类">
      <button :class="{ active: tab === 'names' }" type="button" @click="tab = 'names'"><AppIcon name="write" :size="15" />取名器</button>
      <button :class="{ active: tab === 'map' }" type="button" @click="tab = 'map'"><AppIcon name="grid" :size="15" />地图草图</button>
    </nav>

    <div v-if="tab === 'names'" class="tool-grid">
      <aside class="tool-panel controls-panel">
        <p class="panel-label">NAME GENERATOR</p>
        <h2>为角色找一个能留下来的名字</h2>
        <label>气质
          <select v-model="nameStyle"><option>古典</option><option>清冷</option><option>明快</option><option>异域</option></select>
        </label>
        <label>性别倾向
          <select v-model="nameGender"><option>不限</option><option>女</option><option>男</option></select>
        </label>
        <label>灵感词
          <input v-model="nameSeed" maxlength="20" placeholder="例如：春山、潮汐、旧城" @keyup.enter="generateNames" />
        </label>
        <label>生成数量 <output>{{ nameCount }}</output>
          <input v-model.number="nameCount" type="range" min="3" max="12" />
        </label>
        <button class="tool-primary" type="button" @click="generateNames"><AppIcon name="plus" :size="15" />生成一组名字</button>
        <p class="panel-note">名字由本地词库组合，不会上传人物设定。满意后可复制，再到设定库补充人物档案。</p>
      </aside>

      <div class="tool-panel results-panel">
        <div class="panel-heading"><div><p class="panel-label">RESULTS / {{ names.length }}</p><h2>候选名字</h2></div><span class="seed-chip"># {{ nameSeed || '未命名' }}</span></div>
        <div class="name-list">
          <button v-for="name in names" :key="name" class="name-row" type="button" @click="copyName(name)">
            <strong>{{ name }}</strong><span>{{ copied === name ? '已复制' : '复制' }}</span>
          </button>
        </div>
        <div class="empty-tip">点击任意名字复制到剪贴板。名字只是起点，人物的欲望和选择才会让它真正成立。</div>
      </div>
    </div>

    <div v-else class="map-layout">
      <aside class="tool-panel controls-panel">
        <p class="panel-label">MAP SKETCH / {{ mapVersion + 1 }}</p>
        <h2>先画出关系，再决定地名</h2>
        <label>地貌基调
          <select v-model="terrain"><option>山河</option><option>群岛</option><option>荒原</option></select>
        </label>
        <label>地图种子
          <input v-model="mapSeed" maxlength="20" placeholder="例如：柳溪" />
        </label>
        <label>区域数量 <output>{{ regionCount }}</output>
          <input v-model.number="regionCount" type="range" min="4" max="10" />
        </label>
        <button class="tool-primary" type="button" @click="regenerateMap"><AppIcon name="grid" :size="15" />重新生成草图</button>
        <button class="tool-secondary" type="button" @click="saveToCodex"><AppIcon name="codex" :size="15" />{{ mapSaved ? '已保存到本地草稿' : '保存为设定库草稿' }}</button>
        <p class="panel-note">这是空间关系草图，不是最终地图。拖拽编辑和章节引用会在后续版本接入。</p>
      </aside>

      <div class="tool-panel map-panel">
        <div class="panel-heading"><div><p class="panel-label">ATLAS / {{ terrain }}</p><h2>{{ mapSeed || '未命名' }} · 关系草图</h2></div><span class="seed-chip">{{ regionCount }} 个区域</span></div>
        <svg class="map-canvas" viewBox="0 0 100 100" role="img" :aria-label="`${mapSeed}地图草图`">
          <defs><pattern id="map-grid" width="5" height="5" patternUnits="userSpaceOnUse"><path d="M 5 0 L 0 0 0 5" fill="none" stroke="currentColor" stroke-opacity=".12" stroke-width=".18" /></pattern></defs>
          <rect width="100" height="100" fill="url(#map-grid)" />
          <path v-if="terrain === '山河'" d="M0 74 C18 56 28 67 42 50 S70 29 100 40 M0 84 C24 72 34 85 55 62 S80 52 100 60" fill="none" stroke="currentColor" stroke-opacity=".22" stroke-width="1.2" />
          <path v-else-if="terrain === '群岛'" d="M15 30 C23 22 32 25 34 34 C28 42 18 42 15 30 M60 24 C68 17 78 22 76 31 C68 38 60 34 60 24 M39 68 C48 59 59 64 58 73 C51 82 41 79 39 68" fill="currentColor" fill-opacity=".11" stroke="currentColor" stroke-opacity=".3" stroke-width=".7" />
          <path v-else d="M0 70 C20 58 28 77 45 60 S76 50 100 62 M8 18 C24 25 33 15 46 24 S73 20 94 30" fill="none" stroke="currentColor" stroke-opacity=".24" stroke-width="1.4" stroke-dasharray="2 2" />
          <g v-for="(region, index) in mapRegions" :key="`${region.name}-${index}-${mapVersion}`">
            <circle :cx="region.x" :cy="region.y" r="2.1" class="map-node" />
            <text :x="region.x + 3" :y="region.y + 1.2" class="map-label">{{ region.name }}</text>
          </g>
          <path d="M8 91h15M8 89v4M23 89v4" stroke="currentColor" stroke-width=".6" /><text x="8" y="96" class="map-scale">关系距离 · 草图比例</text>
        </svg>
      </div>
    </div>
  </section>
</template>

<style scoped>
.tools-page { min-height: 100%; padding: 42px clamp(24px, 5vw, 72px) 80px; background: var(--canvas); color: var(--ink); }
.tools-header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; max-width: 1120px; margin: 0 auto 28px; }
.tools-header h1 { margin: 7px 0 8px; font: 600 34px/1.1 var(--font-prose); letter-spacing: .01em; }
.tools-subtitle { margin: 0; color: var(--ink-3); font-size: 14px; }
.tools-stamp { border-left: 1px solid var(--primary-line); padding-left: 14px; color: var(--primary); font: 12px/1.7 var(--font-mono); text-align: right; }
.tools-stamp b { display: block; color: var(--ink-3); font-weight: 400; }
.tools-tabs { display: flex; gap: 2px; max-width: 1120px; margin: 0 auto 18px; border-bottom: 1px solid var(--line-strong); }
.tools-tabs button { display: inline-flex; align-items: center; gap: 8px; border: 0; border-bottom: 2px solid transparent; background: transparent; padding: 12px 16px; color: var(--ink-3); cursor: pointer; }
.tools-tabs button.active { color: var(--primary); border-bottom-color: var(--primary); font-weight: 700; }
.tool-grid, .map-layout { max-width: 1120px; margin: 0 auto; display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 14px; }
.tool-panel { background: var(--panel); border: 1px solid var(--line); min-width: 0; }
.controls-panel { padding: 22px; }
.controls-panel h2, .results-panel h2, .map-panel h2 { margin: 6px 0 22px; font: 600 20px/1.3 var(--font-prose); }
.panel-label { margin: 0; color: var(--ink-4); font: 10px var(--font-mono); letter-spacing: .1em; }
.controls-panel label { display: grid; gap: 7px; margin: 0 0 16px; color: var(--ink-2); font-size: 12px; }
.controls-panel select, .controls-panel input[type='text'], .controls-panel input:not([type]) { width: 100%; height: 34px; border: 1px solid var(--line-strong); background: var(--panel-sunken); color: var(--ink); padding: 0 9px; font: inherit; }
.controls-panel input[type='range'] { width: 100%; accent-color: var(--primary); }
.controls-panel output { float: right; color: var(--primary); font-family: var(--font-mono); }
.tool-primary, .tool-secondary { width: 100%; min-height: 36px; display: inline-flex; align-items: center; justify-content: center; gap: 8px; cursor: pointer; font-size: 13px; }
.tool-primary { border: 1px solid var(--primary); background: var(--primary); color: #fff; }
.tool-secondary { margin-top: 8px; border: 1px solid var(--line-strong); background: transparent; color: var(--ink-2); }
.panel-note { margin: 18px 0 0; color: var(--ink-4); font-size: 11px; line-height: 1.65; }
.results-panel, .map-panel { padding: 22px; }
.panel-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.panel-heading h2 { margin-bottom: 20px; }
.seed-chip { color: var(--primary); background: var(--primary-soft); padding: 5px 8px; font: 11px var(--font-mono); white-space: nowrap; }
.name-list { border-top: 1px solid var(--line); }
.name-row { width: 100%; display: flex; align-items: center; justify-content: space-between; border: 0; border-bottom: 1px solid var(--line); background: transparent; padding: 19px 4px; color: var(--ink); cursor: pointer; text-align: left; }
.name-row:hover { background: var(--panel-sunken); padding-left: 12px; padding-right: 12px; }
.name-row strong { font: 600 25px var(--font-prose); letter-spacing: .12em; }
.name-row span { color: var(--ink-4); font-size: 11px; }
.empty-tip { margin-top: 18px; color: var(--ink-4); font-size: 12px; line-height: 1.7; }
.map-canvas { display: block; width: 100%; min-height: 480px; background: #e9efed; color: #315c5b; border: 1px solid var(--line); }
.map-node { fill: var(--primary); stroke: var(--panel); stroke-width: .9; }
.map-label, .map-scale { fill: currentColor; font-family: var(--font-ui); font-size: 3px; }
.map-scale { font-family: var(--font-mono); font-size: 2.2px; opacity: .65; }
@media (max-width: 760px) { .tools-page { padding: 28px 16px 60px; } .tools-header { display: block; } .tools-stamp { margin-top: 18px; text-align: left; width: max-content; } .tool-grid, .map-layout { grid-template-columns: 1fr; } .map-canvas { min-height: 360px; } }
</style>
