<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import {
  CloseOutlined,
  LeftOutlined,
  ReloadOutlined,
  RightOutlined,
  VerticalLeftOutlined,
  VerticalRightOutlined,
} from '@ant-design/icons-vue'

const props = defineProps({
  tabs: { type: Array, default: () => [] },
  activeKey: { type: String, default: '' },
})

const emit = defineEmits([
  'tab-click',
  'tab-close',
  'tab-close-others',
  'tab-close-left',
  'tab-close-right',
  'tab-close-all',
  'tab-refresh',
])

const scrollRef = ref(null)
const contextMenu = ref({ visible: false, x: 0, y: 0, key: '' })

function onTabClick(key) {
  emit('tab-click', key)
  contextMenu.value.visible = false
}

function onClose(e, key) {
  e.stopPropagation()
  emit('tab-close', key)
}

function onContextMenu(e, key) {
  e.preventDefault()
  contextMenu.value = {
    visible: true,
    x: e.clientX,
    y: e.clientY,
    key,
  }
}

function closeContextMenu() {
  contextMenu.value.visible = false
}

function handleContextAction(action) {
  const key = contextMenu.value.key
  switch (action) {
    case 'close':
      emit('tab-close', key)
      break
    case 'close-others':
      emit('tab-close-others', key)
      break
    case 'close-left':
      emit('tab-close-left', key)
      break
    case 'close-right':
      emit('tab-close-right', key)
      break
    case 'close-all':
      emit('tab-close-all')
      break
    case 'refresh':
      emit('tab-refresh', key)
      break
  }
  closeContextMenu()
}

// Close context menu when clicking outside
function onOverlayClick() {
  closeContextMenu()
}

// Scroll to active tab
watch(
  () => props.activeKey,
  () => {
    nextTick(() => {
      if (!scrollRef.value) return
      const activeEl = scrollRef.value.querySelector('.tab-item.active')
      if (activeEl) {
        activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
      }
    })
  },
)

const contextMenuStyle = computed(() => {
  if (!contextMenu.value.visible) return { display: 'none' }
  return {
    position: 'fixed',
    left: contextMenu.value.x + 'px',
    top: contextMenu.value.y + 'px',
    zIndex: 1050,
  }
})

const contextMenuItems = [
  { key: 'refresh', label: '刷新页面', icon: ReloadOutlined },
  { key: 'close', label: '关闭当前', icon: CloseOutlined, divider: true },
  { key: 'close-left', label: '关闭左侧', icon: VerticalRightOutlined },
  { key: 'close-right', label: '关闭右侧', icon: VerticalLeftOutlined },
  { key: 'close-others', label: '关闭其它', icon: LeftOutlined },
  { key: 'close-all', label: '关闭所有', icon: RightOutlined },
]

function onContextItemClick(item) {
  handleContextAction(item.key)
}
</script>

<template>
  <div class="multi-tab-bar">
    <div class="tab-scroll-area" ref="scrollRef">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-item"
        :class="{ active: activeKey === tab.key }"
        @click="onTabClick(tab.key)"
        @contextmenu="(e) => onContextMenu(e, tab.key)"
      >
        <span class="tab-title">{{ tab.title }}</span>
        <span
          v-if="tab.closable !== false"
          class="tab-close"
          @click="(e) => onClose(e, tab.key)"
        >
          <CloseOutlined />
        </span>
      </button>
    </div>

    <div class="tab-actions">
      <span class="tab-dropdown-btn" title="更多操作" @click="(e) => onContextMenu(e, activeKey)">
        <LeftOutlined style="transform: rotate(-90deg); display: inline-block" />
      </span>
    </div>

    <!-- Context Menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu.visible"
        class="context-menu-overlay"
        @click="onOverlayClick"
      ></div>
      <div
        v-if="contextMenu.visible"
        class="context-menu-dropdown"
        :style="contextMenuStyle"
      >
        <div
          v-for="item in contextMenuItems"
          :key="item.key"
          class="context-menu-item"
          :class="{ 'has-divider': item.divider }"
          @click="onContextItemClick(item)"
        >
          <component :is="item.icon" style="font-size: 14px" />
          <span>{{ item.label }}</span>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.context-menu-overlay {
  position: fixed;
  inset: 0;
  z-index: 1049;
}

.context-menu-dropdown {
  background: var(--bg-elevated);
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius);
  box-shadow: var(--shadow-lg);
  padding: 4px;
  min-width: 150px;
}

.context-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-radius: var(--border-radius-sm);
  cursor: pointer;
  font-size: var(--font-base);
  color: var(--text-primary);
  transition: all var(--transition-fast);
  white-space: nowrap;
}

.context-menu-item:hover {
  background: var(--bg-hover);
}

.context-menu-item.has-divider {
  margin-top: 4px;
  padding-top: 11px;
  border-top: 1px solid var(--border-color);
}
</style>
