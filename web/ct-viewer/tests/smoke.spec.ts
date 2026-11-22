import { expect, test } from '@playwright/test'
import { hasSample, modelReadyMs, noHorizontalScroll, openViewer } from './helpers'

test.skip(!hasSample(), 'sample result missing: run `npm run sample` then `npm run build`')

test('loads the model fast and without errors', async ({ page }) => {
  const c = await openViewer(page)
  const ms = await modelReadyMs(page)
  console.log(`model ready in ${ms.toFixed(0)} ms`)
  expect(ms).toBeLessThan(3000)
  await expect(page.getByText(/98 of 98 shown/)).toBeVisible()
  await expect(page.getByText('Research use only').first()).toBeVisible()
  await expect(page.getByText(/Not for diagnosis, treatment or surgical planning/)).toBeVisible()
  // never claims local-only processing
  await expect(page.locator('body')).not.toContainText(/stays on (this|your) (computer|device)/i)
  const longTasks = await page.evaluate(() => (window as unknown as { __longTasks: number[] }).__longTasks)
  console.log(`long tasks during load: ${longTasks.length}, max ${Math.max(0, ...longTasks).toFixed(0)} ms`)
  expect(c.errors).toEqual([])
})

test('structure list: search, hide, show', async ({ page }) => {
  await openViewer(page)
  await page.getByLabel('Find a structure').fill('liver')
  await expect(page.locator('.row')).toHaveCount(1)
  await page.getByRole('button', { name: 'Hide Liver' }).click()
  await expect(page.getByText(/97 of 98 shown/)).toBeVisible()
  await page.getByRole('button', { name: 'Show Liver' }).click()
  await expect(page.getByText(/98 of 98 shown/)).toBeVisible()
  await page.getByLabel('Find a structure').fill('')
  await page.getByRole('button', { name: /Hide all skeleton/ }).click()
  await expect(page.getByText(/43 of 98 shown/)).toBeVisible()
})

test('hover tooltip and click to isolate', async ({ page }) => {
  await openViewer(page)
  const box = (await page.getByTestId('model-canvas').boundingBox())!
  // the liver sits on the viewer's left of centre in the anterior view
  let found = false
  for (const [fx, fy] of [[0.45, 0.5], [0.42, 0.45], [0.5, 0.55], [0.55, 0.4]]) {
    await page.mouse.move(box.x + box.width * fx, box.y + box.height * fy)
    if (await page.locator('.tooltip').isVisible({ timeout: 2000 }).catch(() => false)) {
      found = true
      await expect(page.locator('.tooltip')).toContainText('mL')
      await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy)
      break
    }
  }
  expect(found).toBe(true)
  await expect(page.locator('.isolate-bar')).toBeVisible()
  await expect(page.locator('.info-card')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.isolate-bar')).toBeHidden()
})

test('cut-by-scan-edge badge is shown', async ({ page }) => {
  await openViewer(page)
  await page.getByLabel('Find a structure').fill('femur, left')
  await page.locator('.row .name').first().click()
  await expect(page.locator('.info-card')).toContainText('Cut by scan edge')
})

test('clipping plane and box with CT on the cut', async ({ page }) => {
  const c = await openViewer(page)
  await page.getByRole('button', { name: 'Plane', exact: true }).click()
  await expect(page.getByLabel('Orientation')).toBeVisible()
  await page.getByLabel('Orientation').selectOption('coronal')
  await page.getByRole('button', { name: 'Box', exact: true }).click()
  await expect(page.getByText('Front edge')).toBeVisible()
  await page.getByRole('button', { name: 'Off', exact: true }).click()
  expect(c.errors).toEqual([])
})

test('camera presets and keyboard orbit', async ({ page }) => {
  const c = await openViewer(page)
  for (const name of ['posterior', 'left', 'right', 'superior', 'inferior', 'anterior']) {
    await page.getByRole('button', { name: new RegExp(`View from ${name}`, 'i') }).click()
  }
  await page.getByTestId('model-canvas').focus()
  await page.keyboard.press('ArrowLeft')
  await page.keyboard.press('+')
  expect(c.errors).toEqual([])
})

test('CT slices: MPR, window presets, volume and oblique', async ({ page }) => {
  const c = await openViewer(page)
  await page.getByRole('tab', { name: 'CT slices' }).click()
  await expect(page.locator('.slice-canvas')).toBeVisible()
  await expect(page.getByText('Loading CT slices…')).toBeHidden({ timeout: 60_000 })
  await page.locator('.slice-view').getByRole('combobox', { name: 'Contrast' }).selectOption('lung')
  await page.getByRole('button', { name: 'Axial', exact: true }).click()
  // window/level drag
  const b = (await page.locator('.slice-canvas').boundingBox())!
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2 + 60, b.y + b.height / 2 + 30, { steps: 5 })
  await page.mouse.up()
  await page.getByRole('button', { name: 'Volume', exact: true }).click()
  await page.getByRole('button', { name: 'Oblique', exact: true }).click()
  await expect(page.locator('.oblique-wrap canvas')).toBeVisible({ timeout: 60_000 })
  expect(c.errors).toEqual([])
})

test('downloads use normal links that resolve', async ({ page, request }) => {
  await openViewer(page)
  await page.getByRole('button', { name: 'Downloads' }).click()
  const dlg = page.getByRole('dialog')
  await expect(dlg).toBeVisible()
  const stl = dlg.getByRole('link', { name: 'Download Liver as STL' })
  const href = await stl.getAttribute('href')
  expect(href).toMatch(/\.stl$/)
  const r = await request.get(href!)
  expect(r.status()).toBe(200)
  for (const name of ['Download Liver as 3MF', 'Download Liver as GLB']) {
    const h = await dlg.getByRole('link', { name }).getAttribute('href')
    expect((await request.get(h!)).status()).toBe(200)
  }
  await page.keyboard.press('Escape')
  await expect(dlg).toBeHidden()
})

test.describe('phone (390 px)', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true })
  test('usable at 390 px', async ({ page }) => {
    const c = await openViewer(page)
    await noHorizontalScroll(page)
    await expect(page.getByRole('button', { name: /View from anterior/i })).toBeVisible()
    await page.getByRole('button', { name: 'Structures' }).click()
    await expect(page.getByLabel('Find a structure')).toBeVisible()
    await page.keyboard.press('Escape')
    await page.getByRole('tab', { name: 'CT slices' }).click()
    await expect(page.getByText('Loading CT slices…')).toBeHidden({ timeout: 60_000 })
    await noHorizontalScroll(page)
    expect(c.errors).toEqual([])
  })
})
