import { spawnSync } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const viewer = path.resolve(here, '..')
const root = path.resolve(viewer, '../..')
const subject = 's0011'
const raw = path.join(root, 'data', 'ct_sample', 'raw', subject)
const output = path.join(viewer, 'public', 'sample')
const localPython = path.join(root, '.runtime', 'python312', 'python.exe')
const python = process.env.CT_SAMPLE_PYTHON || (existsSync(localPython) ? localPython : 'python3')

function run(command, args) {
  const result = spawnSync(command, args, { cwd: viewer, stdio: 'inherit', shell: process.platform === 'win32' && command === 'npx' })
  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status || 1)
}

if (!existsSync(path.join(raw, 'ct.nii.gz'))) {
  run(python, [path.join(here, 'fetch_sample.py'), '--dataset', 'v2-small', '--subject', subject, '--out', path.dirname(raw)])
}
run(python, [path.join(here, 'build_sample.py'), raw, output])
const gltfTransform = path.join(viewer, 'node_modules', '@gltf-transform', 'cli', 'bin', 'cli.js')
run(process.execPath, [gltfTransform, 'meshopt', path.join(output, 'combined_web_raw.glb'), path.join(output, 'combined_web.glb')])
rmSync(path.join(output, 'combined_web_raw.glb'), { force: true })
console.log(`Multi-structure CT sample ready: ${path.relative(viewer, output)}`)
