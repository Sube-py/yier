import { h } from 'vue'
import { createMemoryHistory, createRouter, createWebHistory, type RouterHistory } from 'vue-router'

const RouteAnchor = {
  render() {
    return h('div')
  },
}

export function createAppRouter(history: RouterHistory = createWebHistory(import.meta.env.BASE_URL)) {
  return createRouter({
    history,
    routes: [
      {
        path: '/',
        redirect: '/chat',
      },
      {
        path: '/chat',
        name: 'chat',
        component: RouteAnchor,
      },
      {
        path: '/settings',
        name: 'settings',
        component: RouteAnchor,
      },
      {
        path: '/channel',
        name: 'channel',
        component: RouteAnchor,
      },
    ],
  })
}

export function createTestRouter() {
  return createAppRouter(createMemoryHistory())
}

const router = createAppRouter()

export default router
