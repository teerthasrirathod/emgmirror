import { expect, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

export const SAMPLE = path.join(import.meta.dirname, '..', 'dist', 'sample', 'manifest.json')
export const hasSample = () => fs.existsSync(SAMPLE)

export interface Collected {
  errors: string[]
}

export async function openViewer(page: Page): Promise<Collected> {
  const c: Collected = { errors: [] }
  page.on('pageerror', (e) => c.errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error') c.errors.push(`console: ${m.text()}`)
  })
  await page.addInitScript(() => {
    const w = window as unknown as { __longTasks: number[] }
    w.__longTasks = []
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) w.__longTasks.push(e.duration)
      }).observe({ type: 'longtask', buffered: true })
    } catch {
      /* not supported */
    }
  })
  await page.goto('./')
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
  return c
}

export async function modelReadyMs(page: Page): Promise<number> {
  return page.evaluate(() => (window as unknown as { __ctv: { modelReadyMs: number } }).__ctv.modelReadyMs)
}

export async function noHorizontalScroll(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(over).toBeLessThanOrEqual(0)
}
