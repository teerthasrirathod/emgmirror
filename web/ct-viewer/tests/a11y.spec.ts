import { expect, test, type Page } from '@playwright/test'
import { hasSample, openViewer } from './helpers'

test.skip(!hasSample(), 'sample result missing')

async function contrastIssues(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const parse = (c: string) => (c.match(/[\d.]+/g) || []).map(Number)
    const lum = ([r, g, b]: number[]) => {
      const f = (v: number) => {
        v /= 255
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
      }
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    }
    const bgOf = (el: Element | null): number[] => {
      while (el) {
        const c = parse(getComputedStyle(el).backgroundColor)
        if (c.length >= 3 && (c.length === 3 || c[3] > 0.5)) return c
        el = el.parentElement
      }
      return [255, 255, 255]
    }
    const out: string[] = []
    const els = document.querySelectorAll('button, a, label, p, span, output, option, th, td, input[type=search], .count')
    els.forEach((el) => {
      const h = el as HTMLElement
      if (!h.offsetParent || !h.textContent?.trim()) return
      if (h.closest('.slice-wrap')) return // text over the CT image is drawn by the renderer
      if (h.closest('.sr-only') || h.classList.contains('dot-warn')) return // not visible text
      const st = getComputedStyle(h)
      if (parseFloat(st.opacity) < 1 || (h as HTMLButtonElement).disabled) return
      const fg = parse(st.color)
      const bg = bgOf(h)
      const l1 = lum(fg), l2 = lum(bg)
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
      const large = parseFloat(st.fontSize) >= 18.66 || (parseFloat(st.fontSize) >= 14 && Number(st.fontWeight) >= 700)
      if (ratio < (large ? 3 : 4.5)) out.push(`${h.tagName}.${h.className} "${h.textContent.trim().slice(0, 30)}" ${ratio.toFixed(2)}`)
    })
    return out
  })
}

for (const scheme of ['light', 'dark'] as const) {
  test(`text contrast meets WCAG AA (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await openViewer(page)
    const bad = await contrastIssues(page)
    expect(bad, bad.join('\n')).toEqual([])
  })
}

test('keyboard: skip link, tabs, buttons and dialog', async ({ page }) => {
  await openViewer(page)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to the viewer' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('tab', { name: '3D model' })).toBeFocused()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await expect(page.getByRole('tab', { name: 'CT slices' })).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: '3D model' }).click()
  await page.getByRole('button', { name: 'Downloads' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Close' }).last()).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
})

test('reduced motion: preset changes jump without animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const c = await openViewer(page)
  await page.getByRole('button', { name: /View from left/i }).click()
  const dur = await page.evaluate(() => getComputedStyle(document.querySelector('.spinner') ?? document.body).animationDuration)
  expect(['0s', '1e-06s', '0.000001s', '0.001ms']).toContain(dur)
  expect(c.errors).toEqual([])
})

test('every control has an accessible name', async ({ page }) => {
  await openViewer(page)
  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll('button, input, select, a[href]')]
      .filter((el) => (el as HTMLElement).offsetParent)
      .filter((el) => {
        const h = el as HTMLElement
        const name = h.getAttribute('aria-label') || h.textContent?.trim() || (h.id && document.querySelector(`label[for="${h.id}"]`)?.textContent) || h.closest('label')?.textContent?.trim()
        return !name
      })
      .map((el) => el.outerHTML.slice(0, 80)),
  )
  expect(unnamed).toEqual([])
})
