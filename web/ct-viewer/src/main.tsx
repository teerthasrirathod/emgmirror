import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import UploadHome from './upload/UploadHome'
import './styles.css'

// No result in the address: the home screen is the upload. With ?result=...: the 3D viewer.
const hasResult = new URLSearchParams(location.search).has('result')

createRoot(document.getElementById('root')!).render(<StrictMode>{hasResult ? <App /> : <UploadHome />}</StrictMode>)
