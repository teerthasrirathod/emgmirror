// Device groundwork: labelling, identifiers, sign-off, reference mode, offline,
// and the use-related hazards from docs/ct_platform/device/S3_use_specification.md.
import { expect, test, type Page } from '@playwright/test'
import { hasSample, openViewer } from './helpers'

test.skip(!hasSample(), 'sample result missing')

async function startReview(page: Page) {
  await page.getByRole('button', { name: 'Review', exact: true }).click()
  await page.getByLabel('Your name').fill('Test Reviewer')
  await page.getByLabel('Your role').selectOption({ index: 1 })
  await page.getByRole('button', { name: 'Start review' }).click()
}

async function approve(page: Page) {
  await page.getByRole('tab', { name: 'CT slices' }).click()
  await page.getByRole('tab', { name: '3D model' }).click()
  await page.getByLabel(/I have compared every structure/).check()
  await page.getByRole('button', { name: /Approve version/ }).click()
  await expect(page.getByTestId('approval-status')).toContainText('Approved')
}

test('labelling banner and study identifiers are always shown', async ({ page }) => {
  await openViewer(page)
  await expect(page.getByRole('note')).toHaveText('Research use only. Not for clinical use.')
  const ids = page.getByTestId('study-ids')
  await expect(ids).toContainText('Study date')
  await expect(ids).toContainText('TotalSegmentator s0024')
  await expect(ids).toContainText(/Study ID\s*0e771f9e202d/)
  await expect(page.getByTestId('version-hash')).toHaveText(/^[0-9a-f]{12}$/)
  await page.getByRole('tab', { name: 'CT slices' }).click()
  await expect(ids).toBeVisible()
  await expect(page.getByRole('note')).toBeVisible()
})

test('missing study identifiers are flagged (wrong-patient hazard)', async ({ page }) => {
  await page.route('**/sample/manifest.json', async (route) => {
    const r = await route.fetch()
    const m = await r.json()
    delete m.study
    await route.fulfill({ response: r, json: m })
  })
  await openViewer(page)
  await expect(page.getByText('Study details missing. Check the patient and study before use.')).toBeVisible()
})

test('models open as not approved; approval needs reviewer, slices and attestation', async ({ page }) => {
  await openViewer(page)
  await expect(page.getByTestId('approval-status')).toHaveText('Not approved')
  await startReview(page)
  const approveBtn = page.getByRole('button', { name: /Approve version/ })
  await expect(approveBtn).toBeDisabled()
  await expect(page.locator('.blockers')).toContainText('Open the CT slices to compare.')
  await approve(page)
  const v = await page.getByTestId('version-hash').textContent()
  await expect(page.locator('.review-panel')).toContainText(`for version ${v}`)
})

test('any edit invalidates the approval and changes the version', async ({ page }) => {
  await openViewer(page)
  await startReview(page)
  await approve(page)
  const v1 = await page.getByTestId('version-hash').textContent()
  // exclude a structure from the model
  await page.getByRole('button', { name: 'Close' }).first().click()
  await page.getByLabel('Find a structure').fill('gallbladder')
  await page.locator('.row .name').first().click()
  await page.getByRole('button', { name: 'Exclude from model' }).click()
  await expect(page.getByTestId('approval-status')).toHaveText('Not approved')
  await expect(page.getByTestId('version-hash')).not.toHaveText(v1!)
  await expect(page.locator('.tag-excluded')).toBeVisible()
  // erase a region
  await page.getByRole('button', { name: 'Review', exact: true }).click()
  await page.getByRole('button', { name: 'Erase surface' }).click()
  const box = (await page.getByTestId('model-canvas').boundingBox())!
  for (const [fx, fy] of [[0.45, 0.5], [0.42, 0.45], [0.5, 0.4]]) {
    await page.mouse.click(box.x + box.width * fx, box.y + box.height * fy)
    if ((await page.locator('.edits li').count()) >= 2) break
  }
  await expect(page.locator('.edits li')).toHaveCount(2)
  await expect(page.locator('.review-panel')).toContainText('no longer applies')
  await page.locator('.review-panel details summary').click()
  await expect(page.locator('.audit')).toContainText('invalidate')
  // undo both edits: the version returns to the approved one and the approval applies again
  await page.getByRole('button', { name: /^Undo: Erased/ }).click()
  await page.getByRole('button', { name: /^Undo: Excluded/ }).click()
  await expect(page.getByTestId('version-hash')).toHaveText(v1!)
})

test.describe('stale result', () => {
  // page.route cannot see requests answered by the service worker
  test.use({ serviceWorkers: 'block' })
test('records persist and a changed result is not shown as approved (stale result)', async ({ page }) => {
  await openViewer(page)
  await startReview(page)
  await approve(page)
  await page.reload()
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv)
  await expect(page.getByTestId('approval-status')).toContainText('Approved')
  // the server now delivers a different result under the same address
  await page.route('**/sample/manifest.json', async (route) => {
    const r = await route.fetch()
    const m = await r.json()
    m.structures[0].volume_ml += 1
    await route.fulfill({ response: r, json: m })
  })
  await page.reload()
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv)
  await expect(page.getByTestId('approval-status')).toHaveText('Not approved')
})
})

test('orientation markers follow the camera (wrong-side hazard)', async ({ page }) => {
  await openViewer(page)
  const edge = (e: string) => page.locator(`[data-testid="model-canvas"] [data-edge="${e}"]`)
  await expect(edge('right')).toHaveText('L')
  await expect(edge('left')).toHaveText('R')
  await expect(edge('top')).toHaveText('S')
  await page.getByRole('button', { name: /View from posterior/i }).click()
  await expect(edge('right')).toHaveText('R')
  await expect(edge('left')).toHaveText('L')
  await page.getByRole('button', { name: /View from superior/i }).click()
  await expect(edge('top')).toHaveText('A')
})

test('reference mode: read-only, large targets, no hover-only controls', async ({ page }) => {
  await page.goto('./?mode=reference')
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
  await expect(page.getByRole('alert').filter({ hasText: 'NOT APPROVED' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Review', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Downloads' })).toHaveCount(0)
  await expect(page.locator('input.opacity')).toHaveCount(0)
  for (const b of await page.locator('.toolbar button:visible').all()) {
    const bb = (await b.boundingBox())!
    expect(bb.height).toBeGreaterThanOrEqual(44)
  }
  const box = (await page.getByTestId('model-canvas').boundingBox())!
  await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5)
  await page.waitForTimeout(800)
  await expect(page.locator('.tooltip')).toHaveCount(0)
  for (const e of ['left', 'right', 'top', 'bottom'])
    await expect(page.locator(`[data-testid="model-canvas"] [data-edge="${e}"]`)).toHaveText(/^[LRAPSI]{1,2}$/)
})

test('reference mode works offline once saved', async ({ page, context }) => {
  test.setTimeout(240_000)
  await page.goto('./?mode=reference')
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
  await page.getByRole('tab', { name: 'CT slices' }).click()
  await expect(page.getByText('Loading CT slices…')).toBeHidden({ timeout: 60_000 })
  await page.getByRole('tab', { name: '3D model' }).click()
  await page.getByRole('button', { name: 'Save for offline use' }).click()
  await expect(page.getByText('Saved for offline use', { exact: true })).toBeVisible({ timeout: 120_000 })
  await context.setOffline(true)
  await page.reload()
  await page.waitForFunction(() => (window as unknown as { __ctv?: unknown }).__ctv, null, { timeout: 60_000 })
  await expect(page.getByTestId('study-ids')).toContainText('Offline')
  await expect(page.getByTestId('study-ids')).toContainText('saved for offline use')
  await page.getByRole('tab', { name: 'CT slices' }).click()
  await expect(page.getByText('Loading CT slices…')).toBeHidden({ timeout: 60_000 })
  await expect(page.getByRole('alert').filter({ hasText: /Could not load/ })).toHaveCount(0)
  await context.setOffline(false)
})
