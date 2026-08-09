import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'HuMetric',
  description: 'Entity metric tracking and signal processing platform',
  lang: 'en-US',
  themeConfig: {
    logo: '/logo.svg',
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Quickstart', link: '/quickstart' },
      { text: 'API Reference', link: '/api-reference' },
      { text: 'Architecture', link: '/console-metric-history' },
    ],
    sidebar: {
      '/guide/': [
        {
          text: 'Guides',
          items: [
            { text: 'Authentication', link: '/guide/authentication' },
            { text: 'Signals', link: '/guide/signals' },
            { text: 'Entities', link: '/guide/entities' },
            { text: 'BYO-Key', link: '/guide/byo-key' },
          ]
        }
      ],
      '/console-metric-history': [
        {
          text: 'Architecture',
          items: [
            { text: 'Konsol & Metrik Geçmişi', link: '/console-metric-history' },
          ]
        }
      ]
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/bestekarx/humetric' }
    ],
    footer: {
      message: 'Built with VitePress',
      copyright: 'Copyright © 2026 HuMetric'
    },
    search: {
      provider: 'local'
    }
  }
})
