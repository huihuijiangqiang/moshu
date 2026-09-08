<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useCodexStore } from '@/stores/codex'
import { useShellStore } from '@/stores/shell'
import { useStoryboardStore } from '@/stores/storyboard'
import { useProjectStore } from '@/stores/project'
import type { StoryboardShot, VisualProfile } from '@/types'

const route = useRoute()
const shell = useShellStore()
const codex = useCodexStore()
const storyboard = useStoryboardStore()
const project = useProjectStore()
const profileEditOpen = ref(false)
const savedNotice = ref('')
const episodeCreateOpen = ref(false)
const sceneEditOpen = ref(false)
const sceneCreateMode = ref(false)
const profileCreateOpen = ref(false)
const episodeDraft = reactive({ title: '', sourceChapterIds: [] as string[], targetDuration: 90 })
const sceneDraft = reactive({ purpose: '', summary: '', timeAnchor: '', locationEntryId: '', characterEntryIds: [] as string[] })
const profileDraft = reactive({ codexEntryId: '', displayName: '', style: '', appearance: '', costume: '' })

const projectId = computed(() => typeof route.params.projectId === 'string' ? route.params.projectId : 'p1')
const episodes = computed(() => storyboard.adaptation?.episodes ?? [])
const scenes = computed(() => storyboard.selectedEpisode?.scenes ?? [])
const shots = computed(() => storyboard.selectedScene?.shots ?? [])
const characterEntries = computed(() => codex.entries.filter((entry) => entry.kind === 'character' && entry.status === 'confirmed'))
const locationEntries = computed(() => codex.entries.filter((entry) => entry.kind === 'place' && entry.status === 'confirmed'))
const profileCandidates = computed(() => characterEntries.value.filter((entry) => !storyboard.adaptation?.visualProfiles.some((profile) => profile.codexEntryId === entry.id)))
const activeProfile = computed(() => {
  const characterId = storyboard.selectedScene?.characterEntryIds[0]
  return characterId ? storyboard.adaptation?.visualProfiles.find((profile) => profile.codexEntryId === characterId) ?? null : null
})
const activeCharacters = computed(() => (storyboard.selectedScene?.characterEntryIds ?? []).map((id) => codex.byId.get(id)).filter(Boolean))
const locationName = computed(() => {
  const id = storyboard.selectedScene?.locationEntryId
  return id ? codex.byId.get(id)?.name ?? '未关联地点' : '未关联地点'
})

onMounted(async () => {
  shell.setCrumb('漫剧 · 人物与分镜')
  await Promise.all([codex.load(projectId.value), project.load(projectId.value), storyboard.load(projectId.value)])
})

watch(() => storyboard.selectedSceneId, () => {
  sceneEditOpen.value = false
  sceneCreateMode.value = false
})

function showSaved(message = '已保存') {
  savedNotice.value = message
  window.setTimeout(() => { savedNotice.value = '' }, 1300)
}

function openEpisodeCreate() {
  const number = episodes.value.length + 1
  Object.assign(episodeDraft, { title: `第 ${number} 集 · 待命名`, sourceChapterIds: [], targetDuration: 90 })
  storyboard.clearError()
  episodeCreateOpen.value = true
}

async function createEpisode() {
  if (!episodeDraft.title.trim() || !episodeDraft.sourceChapterIds.length) return
  const episode = await storyboard.createEpisode(projectId.value, {
    title: episodeDraft.title.trim(),
    sourceChapterIds: [...episodeDraft.sourceChapterIds],
    targetDuration: Math.max(1, Math.min(1800, episodeDraft.targetDuration || 90))
  })
  if (!episode) return
  episodeCreateOpen.value = false
  showSaved('漫剧集已创建')
}

function openSceneCreate() {
  Object.assign(sceneDraft, { purpose: '', summary: '', timeAnchor: '', locationEntryId: '', characterEntryIds: [] })
  storyboard.clearError()
  sceneCreateMode.value = true
  sceneEditOpen.value = true
}

function openSceneEdit() {
  const scene = storyboard.selectedScene
  if (!scene) return
  Object.assign(sceneDraft, {
    purpose: scene.purpose,
    summary: scene.summary,
    timeAnchor: scene.timeAnchor,
    locationEntryId: scene.locationEntryId ?? '',
    characterEntryIds: [...scene.characterEntryIds]
  })
  storyboard.clearError()
  sceneCreateMode.value = false
  sceneEditOpen.value = true
}

async function saveScene() {
  if (!sceneDraft.purpose.trim()) return
  const creating = sceneCreateMode.value
  const payload = {
    purpose: sceneDraft.purpose.trim(),
    summary: sceneDraft.summary.trim(),
    timeAnchor: sceneDraft.timeAnchor.trim(),
    locationEntryId: sceneDraft.locationEntryId || undefined,
    characterEntryIds: [...sceneDraft.characterEntryIds]
  }
  const result = creating
    ? await storyboard.createScene(projectId.value, payload)
    : await storyboard.updateScene(projectId.value, payload)
  if (!result) return
  sceneEditOpen.value = false
  showSaved(creating ? '场景已创建' : '场景已保存')
}

function openProfileCreate(characterId?: string) {
  const entry = characterEntries.value.find((item) => item.id === characterId) ?? profileCandidates.value[0]
  Object.assign(profileDraft, { codexEntryId: entry?.id ?? '', displayName: entry?.name ?? '', style: '', appearance: '', costume: '' })
  storyboard.clearError()
  profileCreateOpen.value = true
}

function selectProfileCharacter(id: string) {
  profileDraft.codexEntryId = id
  profileDraft.displayName = characterEntries.value.find((item) => item.id === id)?.name ?? ''
}

async function createVisualProfile() {
  if (!profileDraft.codexEntryId || !profileDraft.displayName.trim()) return
  const profile = await storyboard.createVisualProfile(projectId.value, {
    codexEntryId: profileDraft.codexEntryId,
    displayName: profileDraft.displayName.trim(),
    style: profileDraft.style.trim(),
    appearance: profileDraft.appearance.trim(),
    costume: profileDraft.costume.trim()
  })
  if (!profile) return
  profileCreateOpen.value = false
  showSaved('人物视觉档案已创建')
}

function shotLabel(shot: StoryboardShot) {
  const names: Record<StoryboardShot['shotType'], string> = { wide: '远景', medium: '中景', close: '近景', detail: '特写', overhead: '俯拍' }
  return names[shot.shotType]
}

async function updateShot<K extends keyof StoryboardShot>(key: K, value: StoryboardShot[K]) {
  const updated = await storyboard.updateShot(projectId.value, { [key]: value })
  if (updated) showSaved()
}

async function toggleShotStatus(shot: StoryboardShot) {
  await updateShot('status', shot.status === 'approved' ? 'draft' : 'approved')
}

async function toggleProfileLock(profile: VisualProfile) {
  const updated = await storyboard.updateVisualProfile(projectId.value, profile.id, { locked: !profile.locked })
  if (updated) showSaved()
}

async function profileField(profile: VisualProfile, key: 'style' | 'appearance' | 'costume', event: Event) {
  const value = (event.target as HTMLInputElement).value
  const updated = await storyboard.updateVisualProfile(projectId.value, profile.id, { [key]: value })
  if (updated) showSaved()
}
</script>

<template>
  <div class="storyboard-view">
    <header class="storyboard-head">
      <div>
        <span class="wk-label">改编工作台 / 静态阶段</span>
        <h1>{{ storyboard.adaptation?.title ?? '漫剧改编' }}</h1>
        <p>先锁定人物和镜头语言，再进入图片资产与视频渲染。</p>
      </div>
      <div class="storyboard-head-meta">
        <span class="storyboard-ratio">{{ storyboard.adaptation?.aspectRatio ?? '9:16' }}</span>
        <span>{{ episodes.length }} 集</span>
        <span>{{ storyboard.totalShots }} 镜头</span>
        <span class="storyboard-saved" aria-live="polite">{{ savedNotice }}</span>
      </div>
    </header>
    <div v-if="storyboard.error" class="storyboard-error" role="alert">
      <span>{{ storyboard.error }}</span>
      <button type="button" aria-label="关闭错误" @click="storyboard.clearError"><AppIcon name="close" :size="14" /></button>
    </div>

    <div class="storyboard-layout">
      <aside class="storyboard-outline" aria-label="漫剧集与场景">
        <div class="storyboard-pane-head">
          <div><span class="wk-label">脚本结构</span><strong>集与场景</strong></div>
          <button class="icon-button" type="button" title="新建集" aria-label="新建集" :disabled="storyboard.saving" @click="openEpisodeCreate"><AppIcon name="plus" :size="15" /></button>
        </div>

        <div class="storyboard-tree">
          <section v-for="episode in episodes" :key="episode.id" class="storyboard-episode">
            <button class="storyboard-episode-row" type="button" :aria-selected="episode.id === storyboard.selectedEpisodeId" @click="storyboard.selectEpisode(episode.id)">
              <span class="episode-index">{{ String(episode.number).padStart(2, '0') }}</span>
              <span><strong>{{ episode.title }}</strong><small>{{ episode.targetDuration }} 秒 · {{ episode.scenes.length }} 场 · {{ episode.sourceChapterIds.length }} 章</small></span>
              <span class="storyboard-status" :data-status="episode.status">{{ episode.status === 'in_review' ? '审阅' : '草稿' }}</span>
            </button>
            <div v-if="episode.id === storyboard.selectedEpisodeId" class="storyboard-scene-list">
              <button v-for="scene in episode.scenes" :key="scene.id" class="storyboard-scene-row" type="button" :aria-selected="scene.id === storyboard.selectedSceneId" @click="storyboard.selectScene(scene.id)">
                <span class="scene-index">场 {{ scene.order }}</span>
                <span><strong>{{ scene.purpose || '未命名场景' }}</strong><small>{{ scene.shots.length }} 镜头 · {{ scene.timeAnchor || '时间待定' }}</small></span>
              </button>
              <button class="storyboard-add-scene" type="button" :disabled="storyboard.saving" @click="openSceneCreate"><AppIcon name="plus" :size="13" /> 添加场景</button>
            </div>
          </section>
          <p v-if="!episodes.length" class="storyboard-empty">还没有漫剧集，从第一集开始拆分小说章节。</p>
        </div>

        <div class="storyboard-outline-note">
          <span class="wk-label">工作原则</span>
          <p>每个镜头都引用 Codex 条目，不直接复制人物名字。后续生成图片时会沿用视觉档案版本。</p>
        </div>
      </aside>

      <main class="storyboard-canvas" aria-label="分镜序列">
        <div class="storyboard-canvas-head">
          <div>
            <span class="wk-label">{{ storyboard.selectedEpisode ? `第 ${storyboard.selectedEpisode.number} 集 / 场 ${storyboard.selectedScene?.order ?? '-'}` : '未选择场景' }}</span>
            <h2>{{ storyboard.selectedScene?.purpose || '选择一个场景开始分镜' }}</h2>
            <p>{{ storyboard.selectedScene?.summary || '场景摘要会出现在这里，帮助编剧和画面设计保持同一叙事目标。' }}</p>
          </div>
          <div class="storyboard-canvas-actions">
            <button class="wk-btn" type="button" :disabled="!storyboard.selectedScene || storyboard.saving" @click="openSceneEdit">编辑场景</button>
            <button class="wk-btn" type="button" data-primary="true" :disabled="!storyboard.selectedScene || storyboard.saving" @click="storyboard.createShot(projectId)"><AppIcon name="plus" :size="14" /> 添加镜头</button>
          </div>
        </div>

        <form v-if="sceneEditOpen" class="storyboard-scene-editor" @submit.prevent="saveScene">
          <div class="scene-editor-heading"><strong>{{ sceneCreateMode ? '新建场景' : '编辑场景绑定' }}</strong><button type="button" aria-label="关闭场景编辑" @click="sceneEditOpen = false"><AppIcon name="close" :size="14" /></button></div>
          <label>叙事任务<input v-model="sceneDraft.purpose" maxlength="200" required placeholder="这一场必须完成什么" /></label>
          <label>场景摘要<textarea v-model="sceneDraft.summary" rows="2" placeholder="冲突、转折和离场状态" /></label>
          <div class="scene-editor-columns">
            <label>时间锚点<input v-model="sceneDraft.timeAnchor" maxlength="100" placeholder="次日清晨 / 三年前" /></label>
            <label>地点<select v-model="sceneDraft.locationEntryId"><option value="">未绑定</option><option v-for="entry in locationEntries" :key="entry.id" :value="entry.id">{{ entry.name }}</option></select></label>
          </div>
          <fieldset><legend>出场人物</legend><label v-for="entry in characterEntries" :key="entry.id" class="scene-character-option"><input v-model="sceneDraft.characterEntryIds" type="checkbox" :value="entry.id" /><span>{{ entry.name }}</span></label><p v-if="!characterEntries.length">设定库还没有已确认人物。</p></fieldset>
          <div class="scene-editor-actions"><button class="wk-btn" type="button" @click="sceneEditOpen = false">取消</button><button class="wk-btn" data-primary="true" type="submit" :disabled="storyboard.saving || !sceneDraft.purpose.trim()">{{ storyboard.saving ? '保存中…' : '保存场景' }}</button></div>
        </form>

        <div class="storyboard-filmstrip" :data-empty="!shots.length">
          <article v-for="shot in shots" :key="shot.id" class="storyboard-shot" :data-selected="shot.id === storyboard.selectedShotId" @click="storyboard.selectedShotId = shot.id">
            <button class="storyboard-frame" type="button" :aria-label="`选择第 ${shot.order} 个镜头`" @click.stop="storyboard.selectedShotId = shot.id">
              <span class="frame-corner frame-corner-tl" /><span class="frame-corner frame-corner-br" />
              <span class="frame-number">{{ String(shot.order).padStart(2, '0') }}</span>
              <span class="frame-glyph">{{ shotLabel(shot) }}</span>
              <span class="frame-caption">{{ shot.durationTarget }}s</span>
            </button>
            <div class="storyboard-shot-meta">
              <strong>{{ shot.action || '镜头动作待填写' }}</strong>
              <small>{{ shot.camera }} · {{ shot.status === 'approved' ? '已确认' : '草稿' }}</small>
            </div>
          </article>
          <div v-if="!shots.length" class="storyboard-empty-canvas">
            <span class="empty-mark">+</span>
            <strong>先添加一个镜头</strong>
            <p>镜头会按顺序排列，点击后在右侧编辑景别、动作、对白和画面提示词。</p>
          </div>
        </div>

        <div v-if="storyboard.selectedShot" class="storyboard-shot-editor">
          <div class="storyboard-editor-head">
            <div><span class="wk-label">镜头 {{ String(storyboard.selectedShot.order).padStart(2, '0') }}</span><strong>{{ shotLabel(storyboard.selectedShot) }}</strong></div>
            <button class="wk-btn" type="button" :data-primary="storyboard.selectedShot.status !== 'approved'" @click="toggleShotStatus(storyboard.selectedShot)">{{ storyboard.selectedShot.status === 'approved' ? '退回草稿' : '确认镜头' }}</button>
          </div>
          <div class="storyboard-editor-grid">
            <label>景别<select :value="storyboard.selectedShot.shotType" @change="updateShot('shotType', ($event.target as HTMLSelectElement).value as StoryboardShot['shotType'])"><option value="wide">远景</option><option value="medium">中景</option><option value="close">近景</option><option value="detail">特写</option><option value="overhead">俯拍</option></select></label>
            <label>镜头运动<input :value="storyboard.selectedShot.camera" @change="updateShot('camera', ($event.target as HTMLInputElement).value)" /></label>
            <label>目标时长（秒）<input type="number" min="1" max="120" :value="storyboard.selectedShot.durationTarget" @change="updateShot('durationTarget', Number(($event.target as HTMLInputElement).value))" /></label>
            <label class="editor-wide">动作与表演<textarea rows="2" :value="storyboard.selectedShot.action" placeholder="人物在画面中做什么，动作如何落点" @change="updateShot('action', ($event.target as HTMLTextAreaElement).value)" /></label>
            <label class="editor-wide">对白<textarea rows="2" :value="storyboard.selectedShot.dialogue" placeholder="没有对白可留空" @change="updateShot('dialogue', ($event.target as HTMLTextAreaElement).value)" /></label>
            <label class="editor-wide">旁白<textarea rows="2" :value="storyboard.selectedShot.narration" placeholder="旁白和字幕的初稿" @change="updateShot('narration', ($event.target as HTMLTextAreaElement).value)" /></label>
            <label class="editor-wide">画面提示词<textarea rows="3" :value="storyboard.selectedShot.visualPrompt" placeholder="人物、环境、光线、构图；这里只保存提示词，不生成视频" @change="updateShot('visualPrompt', ($event.target as HTMLTextAreaElement).value)" /></label>
          </div>
        </div>
      </main>

      <aside class="storyboard-inspector" aria-label="人物视觉档案">
        <div class="storyboard-pane-head">
          <div><span class="wk-label">视觉连续性</span><strong>人物档案</strong></div>
          <span class="storyboard-count">{{ storyboard.adaptation?.visualProfiles.length ?? 0 }}</span>
        </div>
        <p class="storyboard-inspector-intro">视觉档案先于图片生成。锁定后，所有镜头都应引用同一版本。</p>
        <div v-if="activeProfile" class="visual-profile">
          <header class="visual-profile-head">
            <div class="visual-profile-avatar">{{ activeProfile.displayName.slice(0, 1) }}</div>
            <div><strong>{{ activeProfile.displayName }}</strong><small>v{{ activeProfile.version }} · {{ activeProfile.locked ? '已锁定' : '可修改' }}</small></div>
            <button class="icon-button" type="button" :title="activeProfile.locked ? '解锁档案' : '锁定档案'" :aria-label="activeProfile.locked ? '解锁档案' : '锁定档案'" @click="toggleProfileLock(activeProfile)">{{ activeProfile.locked ? '锁' : '开' }}</button>
          </header>
          <div class="visual-profile-palette"><span v-for="color in activeProfile.palette" :key="color" :style="{ background: color }" :title="color" /></div>
          <div class="visual-profile-fields" :data-editing="profileEditOpen">
            <label>画风<input :value="activeProfile.style" :disabled="activeProfile.locked || !profileEditOpen" @change="profileField(activeProfile, 'style', $event)" /></label>
            <label>外观锚点<textarea rows="4" :value="activeProfile.appearance" :disabled="activeProfile.locked || !profileEditOpen" @change="profileField(activeProfile, 'appearance', $event)" /></label>
            <label>服装与道具<textarea rows="4" :value="activeProfile.costume" :disabled="activeProfile.locked || !profileEditOpen" @change="profileField(activeProfile, 'costume', $event)" /></label>
          </div>
          <button class="profile-edit" type="button" @click="profileEditOpen = !profileEditOpen">{{ profileEditOpen ? '完成编辑' : '编辑文字档案' }}</button>
          <p v-if="activeProfile.notes" class="visual-profile-note">{{ activeProfile.notes }}</p>
        </div>
        <div v-else-if="activeCharacters.length" class="storyboard-empty inspector-empty">
          <strong>{{ activeCharacters[0]?.name }}尚无视觉档案</strong>
          <p>先建立外观、服装和画风约束，后续镜头会持续引用同一版本。</p>
          <button class="wk-btn" data-primary="true" type="button" :disabled="storyboard.saving" @click="openProfileCreate(activeCharacters[0]?.id)">创建视觉档案</button>
        </div>
        <div v-else class="storyboard-empty inspector-empty">当前场景还没有绑定人物。请先编辑场景并选择出场人物。</div>

        <button v-if="profileCandidates.length" class="profile-create" type="button" :disabled="storyboard.saving" @click="openProfileCreate()"><AppIcon name="plus" :size="13" /> 新建人物视觉档案</button>

        <div class="storyboard-binding">
          <span class="wk-label">当前场景绑定</span>
          <div class="binding-row"><span>地点</span><strong>{{ locationName }}</strong></div>
          <div class="binding-row"><span>人物</span><strong>{{ activeCharacters.map((item) => item?.name).join('、') || '未绑定' }}</strong></div>
          <p>绑定来自设定库 ID，改名或补充人物资料时不会丢失关联。</p>
        </div>
        <div class="storyboard-no-video"><span class="wk-label">当前阶段</span><strong>只做分镜稿</strong><p>图片生成、配音、字幕时间轴和视频合成暂未启用。</p></div>
      </aside>
    </div>

    <div v-if="episodeCreateOpen" class="storyboard-dialog-backdrop" @click.self="episodeCreateOpen = false">
      <form class="storyboard-dialog" role="dialog" aria-modal="true" aria-labelledby="episode-create-title" @submit.prevent="createEpisode">
        <header><div><span class="wk-label">小说 → 漫剧</span><h2 id="episode-create-title">新建漫剧集</h2></div><button type="button" aria-label="关闭" @click="episodeCreateOpen = false"><AppIcon name="close" :size="15" /></button></header>
        <label>集标题<input v-model="episodeDraft.title" maxlength="200" required /></label>
        <label>目标时长（秒）<input v-model.number="episodeDraft.targetDuration" type="number" min="1" max="1800" /></label>
        <fieldset class="episode-chapters"><legend>小说源章节</legend><label v-for="chapter in project.chapters" :key="chapter.id"><input v-model="episodeDraft.sourceChapterIds" type="checkbox" :value="chapter.id" /><span>第 {{ chapter.index }} 章 · {{ chapter.title || '未命名' }}</span></label><p v-if="!project.chapters.length">当前作品还没有可选章节。</p></fieldset>
        <p class="dialog-hint">至少选择一章。章节只作为改编来源，不会复制或修改小说正文。</p>
        <footer><button class="wk-btn" type="button" @click="episodeCreateOpen = false">取消</button><button class="wk-btn" data-primary="true" type="submit" :disabled="storyboard.saving || !episodeDraft.title.trim() || !episodeDraft.sourceChapterIds.length">{{ storyboard.saving ? '创建中…' : '创建并进入' }}</button></footer>
      </form>
    </div>

    <div v-if="profileCreateOpen" class="storyboard-dialog-backdrop" @click.self="profileCreateOpen = false">
      <form class="storyboard-dialog" role="dialog" aria-modal="true" aria-labelledby="profile-create-title" @submit.prevent="createVisualProfile">
        <header><div><span class="wk-label">视觉连续性</span><h2 id="profile-create-title">创建人物视觉档案</h2></div><button type="button" aria-label="关闭" @click="profileCreateOpen = false"><AppIcon name="close" :size="15" /></button></header>
        <label>设定库人物<select :value="profileDraft.codexEntryId" required @change="selectProfileCharacter(($event.target as HTMLSelectElement).value)"><option value="">请选择人物</option><option v-for="entry in profileCandidates" :key="entry.id" :value="entry.id">{{ entry.name }}</option></select></label>
        <label>显示名称<input v-model="profileDraft.displayName" maxlength="200" required /></label>
        <label>画风<input v-model="profileDraft.style" placeholder="半厚涂 / 赛璐璐 / 写实" /></label>
        <label>外观锚点<textarea v-model="profileDraft.appearance" rows="3" placeholder="脸型、发型、年龄感、不可变化的特征" /></label>
        <label>服装与道具<textarea v-model="profileDraft.costume" rows="3" placeholder="常服、身份标志、随身道具" /></label>
        <footer><button class="wk-btn" type="button" @click="profileCreateOpen = false">取消</button><button class="wk-btn" data-primary="true" type="submit" :disabled="storyboard.saving || !profileDraft.codexEntryId || !profileDraft.displayName.trim()">{{ storyboard.saving ? '创建中…' : '创建档案' }}</button></footer>
      </form>
    </div>
  </div>
</template>
