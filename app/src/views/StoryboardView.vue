<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppIcon from '@/components/ui/AppIcon.vue'
import { useCodexStore } from '@/stores/codex'
import { useShellStore } from '@/stores/shell'
import { useStoryboardStore } from '@/stores/storyboard'
import type { StoryboardShot, VisualProfile } from '@/types'

const route = useRoute()
const shell = useShellStore()
const codex = useCodexStore()
const storyboard = useStoryboardStore()
const profileEditOpen = ref(false)
const savedNotice = ref('')

const projectId = computed(() => typeof route.params.projectId === 'string' ? route.params.projectId : 'p1')
const episodes = computed(() => storyboard.adaptation?.episodes ?? [])
const scenes = computed(() => storyboard.selectedEpisode?.scenes ?? [])
const shots = computed(() => storyboard.selectedScene?.shots ?? [])
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
  await Promise.all([codex.load(projectId.value), storyboard.load(projectId.value)])
})

function shotLabel(shot: StoryboardShot) {
  const names: Record<StoryboardShot['shotType'], string> = { wide: '远景', medium: '中景', close: '近景', detail: '特写', overhead: '俯拍' }
  return names[shot.shotType]
}

async function updateShot<K extends keyof StoryboardShot>(key: K, value: StoryboardShot[K]) {
  await storyboard.updateShot(projectId.value, { [key]: value })
  savedNotice.value = '已保存'
  window.setTimeout(() => { savedNotice.value = '' }, 1300)
}

async function toggleShotStatus(shot: StoryboardShot) {
  await updateShot('status', shot.status === 'approved' ? 'draft' : 'approved')
}

async function toggleProfileLock(profile: VisualProfile) {
  await storyboard.updateVisualProfile(projectId.value, profile.id, { locked: !profile.locked })
}

function profileField(profile: VisualProfile, key: 'style' | 'appearance' | 'costume', event: Event) {
  const value = (event.target as HTMLInputElement).value
  storyboard.updateVisualProfile(projectId.value, profile.id, { [key]: value })
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

    <div class="storyboard-layout">
      <aside class="storyboard-outline" aria-label="漫剧集与场景">
        <div class="storyboard-pane-head">
          <div><span class="wk-label">脚本结构</span><strong>集与场景</strong></div>
          <button class="icon-button" type="button" title="新建集" aria-label="新建集" @click="storyboard.createEpisode(projectId)"><AppIcon name="plus" :size="15" /></button>
        </div>

        <div class="storyboard-tree">
          <section v-for="episode in episodes" :key="episode.id" class="storyboard-episode">
            <button class="storyboard-episode-row" type="button" :aria-selected="episode.id === storyboard.selectedEpisodeId" @click="storyboard.selectEpisode(episode.id)">
              <span class="episode-index">{{ String(episode.number).padStart(2, '0') }}</span>
              <span><strong>{{ episode.title }}</strong><small>{{ episode.targetDuration }} 秒 · {{ episode.scenes.length }} 场</small></span>
              <span class="storyboard-status" :data-status="episode.status">{{ episode.status === 'in_review' ? '审阅' : '草稿' }}</span>
            </button>
            <div v-if="episode.id === storyboard.selectedEpisodeId" class="storyboard-scene-list">
              <button v-for="scene in episode.scenes" :key="scene.id" class="storyboard-scene-row" type="button" :aria-selected="scene.id === storyboard.selectedSceneId" @click="storyboard.selectScene(scene.id)">
                <span class="scene-index">场 {{ scene.order }}</span>
                <span><strong>{{ scene.purpose || '未命名场景' }}</strong><small>{{ scene.shots.length }} 镜头 · {{ scene.timeAnchor || '时间待定' }}</small></span>
              </button>
              <button class="storyboard-add-scene" type="button" @click="storyboard.createScene(projectId)"><AppIcon name="plus" :size="13" /> 添加场景</button>
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
          <button class="wk-btn" type="button" data-primary="true" :disabled="!storyboard.selectedScene" @click="storyboard.createShot(projectId)"><AppIcon name="plus" :size="14" /> 添加镜头</button>
        </div>

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
        <div v-else class="storyboard-empty inspector-empty">当前场景还没有绑定人物。请先在设定库建立人物视觉档案。</div>

        <div class="storyboard-binding">
          <span class="wk-label">当前场景绑定</span>
          <div class="binding-row"><span>地点</span><strong>{{ locationName }}</strong></div>
          <div class="binding-row"><span>人物</span><strong>{{ activeCharacters.map((item) => item?.name).join('、') || '未绑定' }}</strong></div>
          <p>绑定来自设定库 ID，改名或补充人物资料时不会丢失关联。</p>
        </div>
        <div class="storyboard-no-video"><span class="wk-label">当前阶段</span><strong>只做分镜稿</strong><p>图片生成、配音、字幕时间轴和视频合成暂未启用。</p></div>
      </aside>
    </div>
  </div>
</template>
