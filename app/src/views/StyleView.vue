<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { styleApi, type StyleProfile } from '@/api/mock/style-profile'
import { useShellStore } from '@/stores/shell'
import { useProjectStore } from '@/stores/project'

const shell = useShellStore()
const project = useProjectStore()
const profiles = ref<StyleProfile[]>([])
const dims = ref<Awaited<ReturnType<typeof styleApi.dimensions>>>([])
const activeId = ref<string | null>(null)

onMounted(async () => {
  shell.setCrumb('风格档')
  profiles.value = await styleApi.list()
  activeId.value = profiles.value.find((p) => p.isDefault)?.id ?? profiles.value[0]?.id ?? null
  if (activeId.value) dims.value = await styleApi.dimensions(activeId.value)
})

const active = computed(() => profiles.value.find((p) => p.id === activeId.value) ?? null)
const wan = (n: number) => (n / 10000).toFixed(0)

async function select(id: string) {
  activeId.value = id
  dims.value = await styleApi.dimensions(id)
}
</script>

<template>
  <div class="wk-pane" :style="{ height: '100%' }">
    <Teleport defer to="#topbar-actions">
      <button class="topbar-btn" type="button">新建风格档</button>
    </Teleport>

    <div class="app-body" :style="{ gridTemplateColumns: '260px 1fr', height: '100%' }">
      <aside class="pane pane-left" :style="{ padding: '16px 0', fontSize: '13px' }">
        <button
          v-for="p in profiles"
          :key="p.id"
          type="button"
          class="side-item"
          :aria-current="p.id === activeId"
          :style="{ display: 'block' }"
          @click="select(p.id)"
        >
          <div :style="{ fontWeight: 700 }">{{ p.name }}</div>
          <div :style="{ marginTop: '4px', opacity: 0.8 }">
            {{ p.isDefault ? '默认 · ' : '' }}样本 {{ wan(p.sampleWords) }} 万字
            <span v-if="p.sampleWords < 50000" :style="{ color: p.id === activeId ? '#fff' : 'var(--color-accent-700)', fontWeight: 700 }">· 样本不足</span>
          </div>
        </button>
        <p class="rule-t muted" :style="{ margin: '16px 16px 0', paddingTop: '16px', lineHeight: 1.7 }">
          风格档只用于约束生成，不会被用作模型训练数据。样本可随时删除。
        </p>
      </aside>

      <main v-if="active" class="pane" :style="{ padding: '30px 34px', background: 'var(--color-neutral-100)', fontSize: '13px' }">
        <div class="row-between" :style="{ alignItems: 'end', marginBottom: '30px' }">
          <div>
            <div :style="{ fontSize: '30px', fontWeight: 700 }">{{ active.name }}</div>
            <div class="muted" :style="{ marginTop: '8px' }">
              来源：{{ active.source }} · 抽取于 {{ active.extractedAt }}
              <template v-if="active.name === project.project?.styleProfile"> · 已应用于当前作品</template>
            </div>
          </div>
          <div v-if="active.alignment" :style="{ textAlign: 'right' }">
            <div class="num" :style="{ fontSize: '44px', color: 'var(--color-accent)' }">{{ active.alignment }}</div>
            <div class="muted" :style="{ marginTop: '6px' }">近 10 章平均对齐度</div>
          </div>
        </div>

        <div class="kicker" :style="{ marginBottom: '16px' }">抽取出的风格维度</div>
        <div class="grid-rule" :style="{ gridTemplateColumns: 'repeat(3, 1fr)', marginBottom: '30px' }">
          <div v-for="d in dims" :key="d.key" :style="{ padding: '20px 18px' }">
            <div :style="{ fontWeight: 700, marginBottom: '10px' }">{{ d.title }}</div>

            <div v-if="d.chart" class="row" :style="{ alignItems: 'end', gap: '3px', height: '40px', marginBottom: '10px' }">
              <span
                v-for="(v, i) in d.chart"
                :key="i"
                :style="{ flex: 1, height: v + '%', background: v === 100 ? 'var(--color-accent)' : 'var(--color-neutral-400)' }"
              />
            </div>

            <div v-else-if="d.ratio" :style="{ display: 'flex', height: '40px', marginBottom: '10px', border: '2px solid var(--color-divider)' }">
              <span :style="{ flex: d.ratio, background: 'var(--color-accent)', color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 700 }">{{ d.ratio }}%</span>
              <span :style="{ flex: 100 - d.ratio, background: 'var(--color-neutral-200)', display: 'flex', alignItems: 'center', justifyContent: 'center' }">{{ 100 - d.ratio }}%</span>
            </div>

            <div v-else-if="d.value" class="num" :style="{ fontSize: '30px', marginBottom: '10px' }">
              {{ d.value }}<span :style="{ fontSize: '14px', fontWeight: 400, color: 'var(--color-neutral-700)' }"> {{ d.unit }}</span>
            </div>

            <div v-else-if="d.tags" class="row" :style="{ flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }">
              <span v-for="t in d.tags" :key="t" class="tag tag-outline" :style="{ fontSize: '11px' }">{{ t }}</span>
            </div>

            <div :style="{ color: 'var(--color-neutral-800)', lineHeight: 1.65 }">{{ d.note }}</div>
          </div>
        </div>

        <template v-if="active.recent.length">
          <div class="kicker" :style="{ marginBottom: '16px' }">近 10 章对齐度</div>
          <div class="row rule-b" :style="{ alignItems: 'end', gap: '6px', height: '70px', paddingBottom: '12px' }">
            <span
              v-for="(v, i) in active.recent"
              :key="i"
              :title="`第 ${i + 1} 章：${v}`"
              :style="{
                flex: 1, height: v + '%',
                background: v < 65 ? 'var(--color-accent-300)' : i === active.recent.length - 1 ? 'var(--color-accent)' : 'var(--color-neutral-400)'
              }"
            />
          </div>
          <p :style="{ margin: '14px 0 0', color: 'var(--color-neutral-800)', lineHeight: 1.7 }">
            最低那一章跌到 {{ Math.min(...active.recent) }} —— 通常是大段战斗描写把形容词密度推高了。
          </p>
        </template>
        <p v-else class="muted" :style="{ lineHeight: 1.7 }">
          样本不足 5 万字，暂不生成风格指纹。继续上传旧作后自动抽取。
        </p>
      </main>
    </div>
  </div>
</template>
