import { createApp } from 'vue'
import PrimeVue from 'primevue/config'
import { definePreset } from '@primeuix/themes'
import Aura from '@primeuix/themes/aura'

import App from './App.vue'
import router from './router'
import 'primeicons/primeicons.css'
import './styles/index.css'

const YierPreset = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{teal.50}',
      100: '{teal.100}',
      200: '{teal.200}',
      300: '{teal.300}',
      400: '{teal.400}',
      500: '{teal.500}',
      600: '{teal.600}',
      700: '{teal.700}',
      800: '{teal.800}',
      900: '{teal.900}',
      950: '{teal.950}',
    },
    colorScheme: {
      light: {
        surface: {
          0: '#fffdf7',
          50: '#f9f5ea',
          100: '#f1ead6',
          200: '#e6dcc1',
          300: '#d2c3a2',
          400: '#b79f7c',
          500: '#997d5d',
          600: '#7d644a',
          700: '#624d39',
          800: '#433428',
          900: '#251d17',
          950: '#16110d',
        },
      },
    },
  },
})

const app = createApp(App)

app.use(PrimeVue, {
  ripple: true,
  theme: {
    preset: YierPreset,
    options: {
      darkModeSelector: false,
      cssLayer: false,
    },
  },
})
app.use(router)

app.mount('#app')
