// Screenshots for docs/ct_platform/results/S3_viewer.md. Run with `npm run screenshots`.
import { test, type Page } from '@playwright/test'
import path from 'node:path'
import { hasSample, openViewer } from './helpers'

const OUT = path.join(import.meta.dirname, '..', '..', '..', 'docs', 'ct_platform', 'results', 'img')
test.skip(!process.env.SCREENSHOTS || !hasSample(), 'set SCREENSHOTS=1 (and build the sample) to write screenshots')

const shot = (page: Page, name: string) => page.screenshot({ path: path.join(OUT, `S3_${name}.jpg`), type: 'jpeg', quality: 82 })
const settle = (page: Page, ms = 2500) => page.waitForTimeout(ms)

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`desktop ${scheme}`, () => {
    test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: scheme })
    test(`desktop ${scheme}`, async ({ page }) => {
      await openViewer(page)
      await settle(page)
      await shot(page, `desktop_${scheme}_model`)
      if (scheme === 'light')
        await page.getByTestId('model-canvas').screenshot({ path: path.join(OUT, 'S3_desktop_light_model_3d.jpg'), type: 'jpeg', quality: 82 })
      await page.getByRole('button', { name: 'Plane', exact: true }).click()
      await page.getByLabel('Orientation').selectOption('coronal')
      await page.getByRole('button', { name: /View from anterior/i }).click()
      await settle(page, 3000)
      await shot(page, `desktop_${scheme}_cut`)
      await page.getByRole('tab', { name: 'CT slices' }).click()
      await page.getByText('Loading CT slices…').waitFor({ state: 'hidden', timeout: 60_000 })
      await settle(page, 3000)
      await shot(page, `desktop_${scheme}_slices`)
      if (scheme === 'dark') {
        await page.getByRole('button', { name: 'Volume', exact: true }).click()
        await page.locator('.slice-view').getByRole('combobox', { name: 'Contrast' }).selectOption('bone')
        await settle(page, 4000)
        await shot(page, `desktop_dark_volume`)
      }
    })
  })
  test.describe(`phone ${scheme}`, () => {
    test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: scheme })
    test(`phone ${scheme}`, async ({ page }) => {
      await openViewer(page)
      await settle(page)
      await shot(page, `phone_${scheme}_model`)
      if (scheme === 'light') {
        await page.getByRole('button', { name: 'Plane', exact: true }).click()
        await page.getByRole('button', { name: /View from superior/i }).click()
        await settle(page, 3000)
        await shot(page, `phone_light_cut`)
      } else {
        await page.getByRole('button', { name: 'Structures' }).click()
        await settle(page, 600)
        await shot(page, `phone_dark_list`)
        await page.keyboard.press('Escape')
        await page.getByRole('tab', { name: 'CT slices' }).click()
        await page.getByText('Loading CT slices…').waitFor({ state: 'hidden', timeout: 60_000 })
        await settle(page, 3000)
        await shot(page, `phone_dark_slices`)
      }
    })
  })
}

test.describe('device groundwork', () => {
  test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: 'light' })
  test('review panel', async ({ page }) => {
    await openViewer(page)
    await page.getByRole('button', { name: 'Review', exact: true }).click()
    await page.getByLabel('Your name').fill('A. Reviewer')
    await page.getByLabel('Your role').selectOption({ index: 1 })
    await page.getByRole('button', { name: 'Start review' }).click()
    await page.getByRole('tab', { name: 'CT slices' }).click()
    await page.getByRole('tab', { name: '3D model' }).click()
    await page.getByRole('button', { name: 'Erase surface' }).click()
    const b = (await page.getByTestId('model-canvas').boundingBox())!
    await page.mouse.click(b.x + b.width * 0.42, b.y + b.height * 0.45)
    await settle(page, 1500)
    await shot(page, 'device_review_not_approved')
    await page.getByLabel(/I have compared every structure/).check()
    await page.getByRole('button', { name: /Approve version/ }).click()
    await settle(page, 1000)
    await shot(page, 'device_review_approved')
  })
  test('reference mode desktop', async ({ page }) => {
    await page.goto('./?mode=reference')
    await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
    await page.getByRole('button', { name: 'Plane', exact: true }).click()
    await page.getByLabel('Orientation').selectOption('coronal')
    await settle(page, 3000)
    await shot(page, 'device_reference_desktop')
  })
})

test.describe('device groundwork phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
  test('reference mode phone', async ({ page }) => {
    await page.goto('./?mode=reference')
    await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
    await settle(page, 2500)
    await shot(page, 'device_reference_phone')
  })
})
