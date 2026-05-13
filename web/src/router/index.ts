import { createMemoryHistory, createRouter, createWebHistory, type RouterHistory } from 'vue-router'

export function createAppRouter(history: RouterHistory = createWebHistory(import.meta.env.BASE_URL)) {
  return createRouter({
    history,
    routes: [
      {
        path: '/',
        redirect: '/chat',
      },
      {
        path: '/login',
        name: 'login',
        component: () => import('../views/LoginView.vue'),
      },
      {
        path: '/codex',
        name: 'codex',
        component: () => import('../views/CodexView.vue'),
      },
      {
        path: '/',
        component: () => import('../views/WorkspaceLayoutView.vue'),
        children: [
          {
            path: 'chat',
            name: 'chat',
            component: () => import('../views/ChatView.vue'),
          },
          {
            path: 'settings',
            name: 'settings',
            component: () => import('../views/SettingsView.vue'),
          },
          {
            path: 'channel',
            name: 'channel',
            component: () => import('../views/ChannelView.vue'),
          },
        ],
      },
    ],
  })
}

export function createTestRouter() {
  return createAppRouter(createMemoryHistory())
}

const router = createAppRouter()

export default router
