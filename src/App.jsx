import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, Check, ChevronRight, CircleHelp, Download, FileCheck2,
  FileText, Headphones, Mic, Moon, Radio, RefreshCw, ShieldCheck,
  Sparkles, Sun, Upload, Wifi, WifiOff, X,
} from 'lucide-react'
import './App.css'

const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, options)
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json()
      detail = body.detail || detail
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new Error(detail)
  }
  const type = response.headers.get('content-type') || ''
  return type.includes('application/json') ? response.json() : response
}

function App() {
  const [session, setSession] = useState(null)
  const [health, setHealth] = useState(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [command, setCommand] = useState('')
  const [isRecording, setIsRecording] = useState(false)
  const [dark, setDark] = useState(false)
  const fileInput = useRef(null)
  const formInput = useRef(null)
  const recorder = useRef(null)
  const audioChunks = useRef([])
  const fieldEditBaseline = useRef(new Map())

  useEffect(() => {
    async function bootstrap() {
      try {
        const [healthData, sessionData] = await Promise.all([
          api('/api/health'),
          api('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: 'offline' }),
          }),
        ])
        setHealth(healthData)
        setSession(sessionData)
      } catch (err) {
        setError(`Could not reach the local DocuVoice backend. ${err.message}`)
      }
    }
    bootstrap()
  }, [])

  const schema = session?.form_schema || null
  const requiredCount = schema?.fields.filter((field) => field.required).length || 0
  const completedRequired = schema?.fields.filter(
    (field) => field.required && session?.form?.fields[field.key]?.value?.trim(),
  ).length || 0
  const completion = requiredCount ? Math.round((completedRequired / requiredCount) * 100) : 0

  function begin(action) {
    setBusy(action)
    setError('')
    setNotice('')
  }

  async function uploadFiles(files) {
    if (!session || !files?.length) return
    begin('upload')
    try {
      let current = session
      for (const file of files) {
        const body = new FormData()
        body.append('file', file)
        current = await api(`/api/sessions/${session.id}/documents`, { method: 'POST', body })
      }
      setSession(current)
      setNotice(`${files.length} document${files.length > 1 ? 's' : ''} processed locally.`)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function loadDemo() {
    if (!session) return
    begin('demo')
    try {
      const updated = await api(`/api/sessions/${session.id}/demo`, { method: 'POST' })
      setSession(updated)
      setNotice('Fabricated identity data loaded. No personal information was used.')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function uploadForm(file) {
    if (!session || !file) return
    begin('form-upload')
    try {
      const body = new FormData()
      body.append('file', file)
      const updated = await api(`/api/sessions/${session.id}/form-template`, { method: 'POST', body })
      setSession(updated)
      setNotice(`${updated.uploaded_form.field_count} AcroForm fields discovered and mapped locally.`)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function updateField(fieldKey, value) {
    if (!session) return
    try {
      const updated = await api(`/api/sessions/${session.id}/fields/${fieldKey}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value }),
      })
      setSession(updated)
    } catch (err) {
      setError(err.message)
    }
  }

  async function runCommand(transcript = command) {
    if (!session || !transcript.trim()) return
    begin('voice')
    try {
      const result = await api(`/api/sessions/${session.id}/voice/command`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transcript }),
      })
      setSession(result.session)
      setNotice(result.message)
      speakResponse(result.message)
      setCommand('')
      if (result.navigate_to) {
        window.setTimeout(() => document.getElementById(`field-${result.navigate_to}`)?.focus(), 50)
      }
      if (result.intent === 'generate_completed_pdf') openPdf()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  async function speakResponse(text) {
    if (!('speechSynthesis' in window)) return
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 0.95
    window.speechSynthesis.speak(utterance)
  }

  async function toggleRecording() {
    if (isRecording) {
      recorder.current?.stop()
      setIsRecording(false)
      return
    }
    if (!health?.whisper_model_cached) {
      setError('Offline speech weights are not cached yet. Typed commands remain available.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream)
      recorder.current = mediaRecorder
      audioChunks.current = []
      mediaRecorder.ondataavailable = (event) => audioChunks.current.push(event.data)
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        const blob = new Blob(audioChunks.current, { type: mediaRecorder.mimeType })
        const body = new FormData()
        body.append('file', blob, 'voice.webm')
        begin('voice')
        try {
          const result = await api(`/api/sessions/${session.id}/voice/transcribe`, { method: 'POST', body })
          setSession(result.session)
          setNotice(result.message)
          speakResponse(result.message)
        } catch (err) {
          setError(err.message)
        } finally {
          setBusy('')
        }
      }
      mediaRecorder.start()
      setIsRecording(true)
    } catch (err) {
      setError(`Microphone unavailable: ${err.message}`)
    }
  }

  async function switchMode() {
    if (!session) return
    const mode = session.mode === 'offline' ? 'online' : 'offline'
    begin('mode')
    try {
      const updated = await api(`/api/sessions/${session.id}/mode`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      })
      setSession(updated)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy('')
    }
  }

  function openPdf() {
    if (session) window.open(`${API}/api/sessions/${session.id}/export`, '_blank', 'noopener')
  }

  const groupedFields = useMemo(() => {
    if (!schema?.fields) return []
    return Object.entries(schema.fields.reduce((groups, field) => {
      groups[field.section] ||= []
      groups[field.section].push(field)
      return groups
    }, {}))
  }, [schema])

  return (
    <div className={dark ? 'app dark' : 'app'}>
      <header className="topbar">
        <a className="brand" href="#top" aria-label="DocuVoice home">
          <span className="brand-mark"><FileText size={19} /></span>
          <span>Docu<span>Voice</span></span>
        </a>
        <div className="header-actions">
          <button className={`mode-pill ${session?.mode || 'offline'}`} onClick={switchMode} disabled={!session || busy === 'mode'}>
            {session?.mode === 'online' ? <Wifi size={15} /> : <WifiOff size={15} />}
            {session?.mode === 'online' ? 'Online' : 'Offline'} mode
          </button>
          <button className="icon-button" onClick={() => setDark((value) => !value)} aria-label="Toggle theme">{dark ? <Sun size={18} /> : <Moon size={18} />}</button>
          <button className="help-button"><CircleHelp size={17} /> Help</button>
        </div>
      </header>

      <main id="top">
        <section className="hero-section">
          {/* <div className="eyebrow"><Sparkles size={14} /> Private by design · works offline</div> */}
          <h1>Documents in. <span>Forms complete.</span></h1>
          <p>Extract identity details locally, review every source, and complete insurance forms with your voice.</p>
          <div className="privacy-line"><ShieldCheck size={17} /> Your documents stay on this computer</div>
        </section>

        {error && <div className="alert error"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => setError('')}><X size={16} /></button></div>}
        {notice && <div className="alert success"><Check size={18} /><span>{notice}</span><button onClick={() => setNotice('')}><X size={16} /></button></div>}

        <div className="workspace-grid">
          <section className="panel upload-panel">
            <div className="panel-heading">
              <div><span className="step-number">1</span><div><h2>Add identity documents</h2><p>Aadhaar, PAN, or another identity PDF/image</p></div></div>
              <span className="local-badge"><WifiOff size={13} /> Local OCR</span>
            </div>
            <div className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); uploadFiles([...event.dataTransfer.files]) }} onClick={() => fileInput.current?.click()} role="button" tabIndex="0">
              <input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" multiple hidden onChange={(event) => uploadFiles([...event.target.files])} />
              <span className="upload-icon">{busy === 'upload' ? <RefreshCw className="spin" /> : <Upload />}</span>
              <strong>{busy === 'upload' ? 'Processing locally…' : 'Drop documents here'}</strong>
              <span>or click to browse · PDF, PNG, JPG · max 15 MB</span>
            </div>
            <div className="demo-row"><span>Nothing safe to upload?</span><button onClick={loadDemo} disabled={!!busy}>Use fabricated demo identity <ChevronRight size={15} /></button></div>
            <div className="document-list">
              {session?.documents.map((document) => (
                <article className="document-card" key={document.id}>
                  <span><FileCheck2 size={20} /></span>
                  <div><strong>{document.filename}</strong><small>{document.document_type.replaceAll('_', ' ')} · {Object.keys(document.fields).length} fields found</small></div>
                  <Check size={18} />
                </article>
              ))}
            </div>
          </section>

          <section className="panel form-choice">
            <div className="panel-heading"><div><span className="step-number">2</span><div><h2>Upload the form</h2><p>AcroForm PDF with fillable fields</p></div></div><span className="local-badge"><FileCheck2 size={13} /> AcroForm</span></div>
            <div
              className={`form-dropzone ${session?.uploaded_form ? 'has-form' : ''}`}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => { event.preventDefault(); uploadForm(event.dataTransfer.files[0]) }}
              onClick={() => formInput.current?.click()}
              role="button"
              tabIndex="0"
            >
              <input ref={formInput} type="file" accept=".pdf,application/pdf" hidden onChange={(event) => uploadForm(event.target.files[0])} />
              {session?.uploaded_form ? (
                <>
                  <span className="form-ready-icon"><Check size={18} /></span>
                  <strong>{session.uploaded_form.filename}</strong>
                  <small>{session.uploaded_form.page_count} page{session.uploaded_form.page_count !== 1 ? 's' : ''} · {session.uploaded_form.field_count} fillable fields</small>
                  <em>Click or drop another PDF to replace it</em>
                </>
              ) : (
                <>
                  <span className="upload-icon">{busy === 'form-upload' ? <RefreshCw className="spin" /> : <Upload />}</span>
                  <strong>{busy === 'form-upload' ? 'Inspecting form…' : 'Drop an AcroForm PDF here'}</strong>
                  <small>Flat PDFs are not supported in this phase</small>
                </>
              )}
            </div>
            <p className="form-help">Field names, tooltips, types, options and required flags are read directly from the PDF. Recognized identity values are mapped without changing the original layout.</p>
          </section>
        </div>

        {session?.form && schema && (
          <section className="review-shell">
            <div className="review-header">
              <div><span className="step-number">3</span><div><h2>Review and complete</h2><p>{schema.title}</p></div></div>
              <div className="progress-wrap">
                <span>{requiredCount ? `${completion}% required fields complete` : `${schema.fields.length} fillable fields discovered`}</span>
                <div><i style={{ width: `${requiredCount ? completion : 100}%` }} /></div>
              </div>
            </div>
            <div className="review-grid">
              <div className="fields-column">
                {groupedFields.map(([section, fields]) => (
                  <fieldset key={section}>
                    <legend>{section}</legend>
                    <div className="field-grid">
                      {fields.map((field) => {
                        const value = session.form.fields[field.key]
                        const issue = session.form.validation_issues.find((item) => item.field === field.key)
                        const setLocalValue = (nextValue) => setSession((current) => ({ ...current, form: { ...current.form, fields: { ...current.form.fields, [field.key]: { ...value, value: nextValue } } } }))
                        const updateLocal = (event) => {
                          if (!fieldEditBaseline.current.has(field.key)) fieldEditBaseline.current.set(field.key, value.value)
                          setLocalValue(event.target.value)
                        }
                        const commitEdit = (event) => {
                          if (!fieldEditBaseline.current.has(field.key)) return
                          const original = fieldEditBaseline.current.get(field.key)
                          fieldEditBaseline.current.delete(field.key)
                          if (event.target.value.trim() !== original.trim()) updateField(field.key, event.target.value)
                        }
                        const common = { id: `field-${field.key}`, name: `docuvoice_${field.key}`, autoComplete: 'off', value: value.value, disabled: field.read_only, maxLength: field.max_length || undefined, onChange: updateLocal, onBlur: commitEdit }
                        return (
                          <div key={field.key} className={`field-control ${field.field_type === 'textarea' ? 'wide' : ''}`}>
                            <label htmlFor={`field-${field.key}`}>{field.label}{field.required && <b>*</b>}</label>
                            {value.options?.length > 1 && (
                              <div className="conflict-picker" role="radiogroup" aria-label={`Available values for ${field.label}`}>
                                <strong>Choose a value found in your documents</strong>
                                {value.options.map((option) => {
                                  const sourceNames = [...new Set(option.evidence.map((item) => item.document_name))]
                                  const selected = value.value === option.value
                                  return (
                                    <button
                                      type="button"
                                      role="radio"
                                      aria-checked={selected}
                                      className={selected ? 'conflict-option selected' : 'conflict-option'}
                                      key={`${field.key}-${option.value}`}
                                      onClick={() => updateField(field.key, option.value)}
                                    >
                                      <i>{selected && <Check size={12} />}</i>
                                      <span><b>{option.value}</b><small>From {sourceNames.join(', ')}</small></span>
                                    </button>
                                  )
                                })}
                                <small className="custom-value-hint">Or enter a different value below</small>
                              </div>
                            )}
                            {field.field_type === 'textarea' && <textarea {...common} />}
                            {(field.field_type === 'select' || field.field_type === 'radio') && (
                              <select {...common}>
                                <option value="">Select a value</option>
                                {field.options.map((option) => <option value={option} key={option}>{option}</option>)}
                              </select>
                            )}
                            {field.field_type === 'checkbox' && (
                              <label className="checkbox-control">
                                <input
                                  id={`field-${field.key}`}
                                  type="checkbox"
                                  disabled={field.read_only}
                                  checked={Boolean(value.value)}
                                  onChange={(event) => {
                                    const nextValue = event.target.checked ? (field.options[0] || 'true') : ''
                                    setLocalValue(nextValue)
                                    updateField(field.key, nextValue)
                                  }}
                                />
                                <span>{value.value ? 'Selected' : 'Not selected'}</span>
                              </label>
                            )}
                            {!['textarea', 'select', 'radio', 'checkbox'].includes(field.field_type) && <input {...common} type={field.field_type === 'email' ? 'email' : 'text'} />}
                            <small className={value.source === 'conflict' ? 'source conflict' : 'source'}>
                              {value.source === 'document' && <><FileCheck2 size={12} /> From {value.source_document_name}{value.confidence != null ? ` · OCR ${Math.round(value.confidence * 100)}%` : ''}</>}
                              {value.source === 'user' && <><Check size={12} /> {value.source_document_name ? `Selected by you from ${value.source_document_name}` : 'Entered and confirmed by you'}</>}
                              {value.source === 'conflict' && <><AlertTriangle size={12} /> Conflicting document values — review required</>}
                              {value.source === 'empty' && 'Not found in uploaded documents'}
                            </small>
                            {issue && <small className={`inline-issue ${issue.severity}`}>{issue.message}</small>}
                          </div>
                        )
                      })}
                    </div>
                  </fieldset>
                ))}
              </div>

              <aside className="assistant-card">
                <div className="assistant-title"><span><Headphones size={19} /></span><div><strong>Voice assistant</strong><small>Offline command mode</small></div><i><Radio size={13} /> Local</i></div>
                <div className="assistant-copy"><p>Tell me what to change or ask what is missing.</p><div className="suggestions"><button onClick={() => setCommand('Change my pincode to 400056')}>“Change my pincode…”</button><button onClick={() => setCommand('Which fields are still missing?')}>“Which fields are missing?”</button></div></div>
                <div className="command-box"><input value={command} onChange={(event) => setCommand(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && runCommand()} placeholder="Type a voice command…" /><button className={isRecording ? 'mic recording' : 'mic'} onClick={toggleRecording} aria-label="Record voice command"><Mic size={18} /></button><button className="send" onClick={() => runCommand()} disabled={!command.trim() || busy === 'voice'}><ChevronRight size={18} /></button></div>
                {!health?.whisper_model_cached && <small className="model-note"><AlertTriangle size={13} /> Speech model not cached; typed commands work.</small>}
                <div className="validation-summary">
                  <strong>Validation</strong>
                  {session.form.validation_issues.length ? <ul>{session.form.validation_issues.slice(0, 4).map((issue, index) => <li key={`${issue.code}-${index}`} className={issue.severity}><AlertTriangle size={14} />{issue.message}</li>)}</ul> : <p><ShieldCheck size={16} /> No issues found</p>}
                </div>
                <button className="export-button" onClick={openPdf}><Download size={17} /> Preview completed PDF</button>
              </aside>
            </div>
          </section>
        )}
      </main>
      <footer><span><ShieldCheck size={15} /> Local-first document processing</span></footer>
    </div>
  )
}

export default App
