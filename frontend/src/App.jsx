import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AlertTriangle, CheckCircle2, Clapperboard, Download, Film, Loader2, Play, RefreshCw, UploadCloud, Wand2 } from 'lucide-react';
import './styles.css';

const query = new URLSearchParams(window.location.search);
const API_BASE = query.get('api') || import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const initialGenerationMode = query.get('agent') === 'false' ? 'workflow' : (query.get('mode') || 'agent');
const defaultTheme = '大学生工作难找，投简历像进黑洞，岗位要求越来越离谱';

function App() {
  const [theme, setTheme] = useState(defaultTheme);
  const [instruction, setInstruction] = useState('更讽刺一点，但结尾温暖一点');
  const [durationMode, setDurationMode] = useState('short');
  const [generationMode, setGenerationMode] = useState(initialGenerationMode === 'workflow' ? 'workflow' : 'agent');
  const [candidates, setCandidates] = useState([]);
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [plan, setPlan] = useState(null);
  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState('');
  const [error, setError] = useState('');
  const [streamStatus, setStreamStatus] = useState(null);
  const [draftCandidates, setDraftCandidates] = useState([]);
  const [storyboardStatus, setStoryboardStatus] = useState(null);
  const [suggestions, setSuggestions] = useState([]);
  const [briefSuggestions, setBriefSuggestions] = useState({});
  const [briefSuggestionProvider, setBriefSuggestionProvider] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [viralUpload, setViralUpload] = useState(null);
  const [viralJob, setViralJob] = useState(null);
  const [materialUploads, setMaterialUploads] = useState([]);
  const [viralDescription, setViralDescription] = useState('');
  const [materialDescription, setMaterialDescription] = useState('');
  const [creativeBrief, setCreativeBrief] = useState(defaultCreativeBrief());
  const candidateRunRef = useRef(0);
  const briefSuggestRef = useRef(0);

  const timeline = plan?.timeline || [];
  const videoUrl = job?.video_url ? `${API_BASE}${job.video_url}` : '';
  const videoName = job?.output_path?.split('/').pop() || 'maomeme-video.mp4';
  const canRender = plan && job?.status !== 'running' && loading !== 'render';
  const candidateDisplayList = useMemo(
    () => displayCandidates({ loading, candidates, draftCandidates }),
    [loading, candidates, draftCandidates]
  );

  useEffect(() => {
    const text = theme.trim();
    if (text.length < 4) {
      setBriefSuggestions({});
      return undefined;
    }
    const runId = briefSuggestRef.current + 1;
    briefSuggestRef.current = runId;
    const timer = window.setTimeout(async () => {
      try {
        const data = await request('/api/maomeme/brief-suggestions', {
          theme: text,
          creative_brief: creativeBrief,
          session_id: sessionId || null,
          viral_analysis_id: viralJob?.analysis_id || null
        });
        if (briefSuggestRef.current !== runId) return;
        setBriefSuggestions(data.suggestions || {});
        setBriefSuggestionProvider(data.provider || '');
      } catch {
        if (briefSuggestRef.current === runId) setBriefSuggestions({});
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [theme, sessionId, viralJob?.analysis_id]);

  async function request(path, body) {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok || data.status === 'error') {
      throw new Error(data.detail || data.message || '请求失败');
    }
    return data;
  }

  async function uploadForm(path, formData) {
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      body: formData
    });
    const data = await response.json();
    if (!response.ok || data.status === 'error') {
      throw new Error(data.detail || data.message || '上传失败');
    }
    return data;
  }

  async function generateCandidates() {
    const runId = candidateRunRef.current + 1;
    candidateRunRef.current = runId;
    setLoading('candidates');
    setError('');
    setJob(null);
    setCandidates([]);
    setSelectedCandidate(null);
    setPlan(null);
    setDraftCandidates(streamingCandidatePlaceholders());
    setStreamStatus({ message: '准备生成候选剧本', progress: 0.05 });
    try {
      let rawAgentText = '';
      const data = await streamRequest('/api/maomeme/candidates-stream', {
        theme,
        ...modePayload(generationMode),
        duration_mode: durationMode,
        ...contextPayload()
      }, (event) => {
        if (candidateRunRef.current !== runId) return;
        if (event.type === 'agent_delta' && event.text) {
          rawAgentText += event.text;
          setDraftCandidates((items) => normalizeCandidateList(mergeDraftCandidates(items, extractCandidateDrafts(rawAgentText)), { fillPlaceholders: true }));
        }
        if (event.type === 'draft_candidate' && event.candidate) {
          setDraftCandidates((items) => replaceCandidateByPosition(items, { ...event.candidate, streaming: true }));
        }
        if (event.type === 'candidate' && event.candidate) {
          setCandidates((items) => replaceCandidateByPosition(items, { ...event.candidate, streaming: false }));
          setDraftCandidates((items) => replaceCandidateByPosition(items, { ...event.candidate, streaming: false }));
        }
      });
      if (candidateRunRef.current !== runId) return;
      setCandidates(normalizeCandidateList(data.candidates || []));
      setSelectedCandidate(null);
      setPlan(null);
      loadSuggestions({ candidate: data.candidates?.[0] || null, plan: null });
    } catch (err) {
      if (candidateRunRef.current === runId) setError(err.message);
    } finally {
      if (candidateRunRef.current === runId) {
        setLoading('');
        setDraftCandidates([]);
        window.setTimeout(() => setStreamStatus(null), 900);
      }
    }
  }

  async function streamRequest(path, body, onEvent, options = {}) {
    const updateMainStatus = options.updateMainStatus !== false;
    const response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || '流式请求失败');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((item) => item.startsWith('data: '));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (updateMainStatus) setStreamStatus({ message: event.message || '生成中', progress: event.progress || 0 });
        onEvent?.(event);
        if (event.type === 'error') throw new Error(event.message || '生成失败');
        if (event.type === 'done') finalData = event;
      }
    }

    if (!finalData) throw new Error('流式生成没有返回结果');
    return finalData;
  }

  async function selectCandidate(candidate) {
    candidateRunRef.current += 1;
    setLoading(`select-${candidate.id}`);
    setError('');
    setJob(null);
    setPlan({ id: 'streaming-plan', theme, timeline: [], script: [] });
    setStoryboardStatus({ message: '准备生成分镜时间线', progress: 0.08 });
    try {
      const data = await streamRequest('/api/maomeme/select-stream', {
        theme,
        candidate,
        ...modePayload(generationMode),
        duration_mode: durationMode,
        ...contextPayload()
      }, (event) => {
        setStoryboardStatus({ message: event.message || '分镜生成中', progress: event.progress || 0 });
        if (event.type === 'stage' && event.script) {
          setPlan((current) => ({ ...(current || {}), script: event.script, timeline: [] }));
        }
        if (event.type === 'slot' && event.slot) {
          setPlan((current) => ({
            ...(current || { id: 'streaming-plan', theme }),
            timeline: upsertTimelineById(current?.timeline || [], normalizeSlot(event.slot))
          }));
        }
        if (event.type === 'slot_patch' && event.slot) {
          setPlan((current) => ({
            ...(current || { id: 'streaming-plan', theme }),
            timeline: upsertTimelineById(current?.timeline || [], normalizeSlot(event.slot))
          }));
        }
      }, { updateMainStatus: false });
      setSelectedCandidate(candidate);
      setPlan(data.plan);
      loadSuggestions({ candidate, plan: data.plan });
      setJob(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading('');
      window.setTimeout(() => setStoryboardStatus(null), 900);
    }
  }

  async function revisePlan() {
    if (!instruction.trim()) return;
    const candidateForRevision = selectedCandidate || candidates[0] || null;
    if (!plan && !candidateForRevision) {
      setError('请先生成候选剧本，再进行自然语言调整');
      return;
    }
    setLoading('revise');
    setError('');
    setJob(null);
    try {
      const data = await request('/api/maomeme/revise', {
        theme,
        instruction,
        plan,
        candidate: candidateForRevision,
        ...modePayload(generationMode),
        duration_mode: durationMode,
        ...contextPayload()
      });
      setSelectedCandidate(data.candidate);
      setPlan(data.plan);
      loadSuggestions({ candidate: data.candidate, plan: data.plan });
      if (!plan) {
        setCandidates((items) => {
          if (!items.length) return [data.candidate];
          return [data.candidate, ...items.slice(1)];
        });
      }
      setJob(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading('');
    }
  }

  async function loadSuggestions({ candidate = selectedCandidate, plan: activePlan = plan } = {}) {
    try {
      const data = await request('/api/maomeme/revision-suggestions', {
        theme,
        candidate,
        plan: activePlan,
        generation_mode: generationMode,
        duration_mode: durationMode,
        creative_brief: creativeBrief
      });
      setSuggestions(data.suggestions || []);
    } catch {
      setSuggestions([
        '更讽刺一点，但结尾留一点温暖',
        '减少说教，多一点具体生活细节',
        '加强两只猫对话冲突'
      ]);
    }
  }

  async function renderVideo() {
    if (!plan) return;
    setLoading('render');
    setError('');
    try {
      const data = await request('/api/maomeme/render-jobs', {
        plan,
        packaging_engine: 'hyperframes',
        allow_ai_fill: Boolean(creativeBrief.allow_ai_fill)
      });
      setJob(data.job);
      pollJob(data.job.job_id);
    } catch (err) {
      setError(err.message);
      setLoading('');
    }
  }

  async function uploadViralVideo(event) {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setLoading('upload-viral');
    setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      if (sessionId) form.append('session_id', sessionId);
      form.append('description', viralDescription || creativeBrief.viral_topic || theme);
      const data = await uploadForm('/api/uploads/viral-video', form);
      setSessionId(data.session_id);
      const upload = data.uploads?.[0] || null;
      setViralUpload(upload);
      setViralJob(null);
      if (upload) await startViralAnalysis(data.session_id, upload.upload_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading('');
    }
  }

  async function uploadMaterials(event) {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    setLoading('upload-materials');
    setError('');
    try {
      const form = new FormData();
      files.forEach((file) => form.append('files', file));
      if (sessionId) form.append('session_id', sessionId);
      form.append('description', materialDescription || '用户上传素材');
      const data = await uploadForm('/api/uploads/materials', form);
      setSessionId(data.session_id);
      setMaterialUploads((items) => mergeUploads(items, data.uploads || []));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading('');
    }
  }

  async function startViralAnalysis(activeSessionId = sessionId, uploadId = viralUpload?.upload_id) {
    if (!activeSessionId || !uploadId) return;
    setLoading('analyze-viral');
    setError('');
    try {
      const data = await request('/api/analyze/viral-jobs', {
        session_id: activeSessionId,
        upload_id: uploadId,
        use_doubao: true,
        creative_brief: creativeBrief
      });
      setViralJob(data.job);
      watchViralJob(data.job.job_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading('');
    }
  }

  async function watchViralJob(jobId) {
    try {
      await streamGet(`/api/analyze/viral-jobs/${jobId}/stream`, (event) => {
        if (event.job) setViralJob(event.job);
        if (event.type === 'error') throw new Error(event.message || '爆款分析失败');
      });
    } catch {
      pollViralJob(jobId);
    }
  }

  async function streamGet(path, onEvent) {
    const response = await fetch(`${API_BASE}${path}`);
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || '状态流连接失败');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() || '';
      for (const chunk of chunks) {
        const line = chunk.split('\n').find((item) => item.startsWith('data: '));
        if (!line) continue;
        onEvent?.(JSON.parse(line.slice(6)));
      }
    }
  }

  async function pollViralJob(jobId) {
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/api/analyze/viral-jobs/${jobId}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '爆款分析轮询失败');
        setViralJob(data.job);
        if (['done', 'error'].includes(data.job.status)) {
          window.clearInterval(timer);
          if (data.job.status === 'error') setError(data.job.error || '爆款分析失败');
        }
      } catch (err) {
        window.clearInterval(timer);
        setError(err.message);
      }
    }, 800);
  }

  function contextPayload() {
    return {
      session_id: sessionId || null,
      viral_analysis_id: viralJob?.analysis_id || null,
      user_material_ids: materialUploads.map((item) => item.upload_id),
      creative_brief: creativeBrief
    };
  }

  async function pollJob(jobId) {
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/api/maomeme/render-jobs/${jobId}`);
        const data = await response.json();
        if (response.status === 404) {
          throw new Error('后端已重启，当前渲染任务状态丢失；请重新生成视频');
        }
        if (!response.ok) throw new Error(data.detail || '轮询失败');
        setJob(data.job);
        if (['done', 'error'].includes(data.job.status)) {
          window.clearInterval(timer);
          setLoading('');
          if (data.job.status === 'error') setError(data.job.error || '渲染失败');
        }
      } catch (err) {
        window.clearInterval(timer);
        setLoading('');
        setError(err.message);
      }
    }, 700);
  }

  const stats = useMemo(() => [
    { label: '候选剧本', value: candidates.length || '-' },
    { label: '分镜数量', value: timeline.length || '-' },
    { label: '渲染状态', value: job?.status || '-' },
    { label: '包装引擎', value: job?.packaging_engine || 'hyperframes' }
  ], [candidates.length, timeline.length, job]);

  return (
    <main className="app">
      <section className="topbar">
        <div className="brand"><Clapperboard size={24} /><span>MaoMeme</span></div>
        <div className="actions">
          <button onClick={generateCandidates} disabled={loading === 'candidates'}>
            {loading === 'candidates' ? <Loader2 className="spin" size={18} /> : <Wand2 size={18} />}
            生成候选
          </button>
          <button className="primary" onClick={renderVideo} disabled={!canRender}>
            {loading === 'render' ? <Loader2 className="spin" size={18} /> : <Play size={18} />}
            生成视频
          </button>
        </div>
      </section>

      <section className="workspace">
        <aside className="panel left">
          <h1>猫 meme 爆款结构迁移</h1>
          <p>先写清主题、参考视频和生成约束。Workflow 稳定出片，Agent 后续做深度优化。</p>
          <div className="assetGrid">
            {stats.map((item) => (
              <div className="assetCard" key={item.label}>
                <strong>{item.value}</strong>
                <span>{item.label}</span>
              </div>
            ))}
          </div>
          <div className="promptBox">
            <label>主题</label>
            <textarea value={theme} onChange={(event) => setTheme(event.target.value)} />
            <BriefSuggestionPanel
              suggestions={briefSuggestions}
              provider={briefSuggestionProvider}
              onPick={(field, value) => setCreativeBrief((current) => ({ ...current, [field]: value }))}
            />
            <label>爆款视频主题 / 原视频想表达什么</label>
            <input
              value={creativeBrief.viral_topic}
              onChange={(event) => setCreativeBrief((value) => ({ ...value, viral_topic: event.target.value }))}
              placeholder="例如：请假被拒后反套路整顿职场"
            />
            <div className="briefGrid">
              <input
                value={creativeBrief.target_audience}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, target_audience: event.target.value }))}
                placeholder="目标受众"
              />
              <input
                value={creativeBrief.protagonist}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, protagonist: event.target.value }))}
                placeholder="主角猫设定"
              />
              <input
                value={creativeBrief.core_conflict}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, core_conflict: event.target.value }))}
                placeholder="核心冲突"
              />
              <input
                value={creativeBrief.ending_tone}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, ending_tone: event.target.value }))}
                placeholder="结尾倾向"
              />
            </div>
            <details className="advancedBrief">
              <summary>生成约束</summary>
              <small>这些字段会影响剧本、背景和道具选择；补全建议不会自动覆盖。</small>
              <input
                value={creativeBrief.style}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, style: event.target.value }))}
                placeholder="整体风格：讽刺 / 温暖 / 荒诞"
              />
              <input
                value={creativeBrief.required_scenes}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, required_scenes: event.target.value }))}
                placeholder="必须出现的场景"
              />
              <input
                value={creativeBrief.required_props}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, required_props: event.target.value }))}
                placeholder="必须出现的道具"
              />
              <input
                value={creativeBrief.avoid_content}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, avoid_content: event.target.value }))}
                placeholder="不要出现的内容"
              />
              <input
                value={creativeBrief.main_cat_count}
                onChange={(event) => setCreativeBrief((value) => ({ ...value, main_cat_count: event.target.value }))}
                placeholder="主角猫数量，例如 1-2 只"
              />
              <label className="toggleRow">
                <input
                  type="checkbox"
                  checked={creativeBrief.allow_multi_cat}
                  onChange={(event) => setCreativeBrief((value) => ({ ...value, allow_multi_cat: event.target.checked }))}
                />
                允许办公室/群像场景出现多只猫
              </label>
              <label className="toggleRow">
                <input
                  type="checkbox"
                  checked={creativeBrief.allow_ai_fill}
                  onChange={(event) => setCreativeBrief((value) => ({ ...value, allow_ai_fill: event.target.checked }))}
                />
                允许缺素材时用 AI 补图
              </label>
            </details>
            <div className="uploadPanel">
              <div className="uploadHead">
                <strong>爆款参考视频</strong>
                {viralJob?.analysis_id && <span>已分析</span>}
              </div>
              <input value={viralDescription} onChange={(event) => setViralDescription(event.target.value)} placeholder="给参考视频补一句描述" />
              <label className="fileButton">
                {loading === 'upload-viral' || loading === 'analyze-viral' ? <Loader2 className="spin" size={16} /> : <UploadCloud size={16} />}
                上传并分析爆款视频
                <input type="file" accept="video/*" onChange={uploadViralVideo} />
              </label>
              {viralUpload && <small>{viralUpload.filename} · {formatBytes(viralUpload.size_bytes)}</small>}
              {viralJob && <AnalysisCard job={viralJob} />}
            </div>
            <div className="uploadPanel">
              <div className="uploadHead">
                <strong>我的素材</strong>
                <span>{materialUploads.length} 个</span>
              </div>
              <input value={materialDescription} onChange={(event) => setMaterialDescription(event.target.value)} placeholder="给这批素材补一句描述" />
              <label className="fileButton">
                {loading === 'upload-materials' ? <Loader2 className="spin" size={16} /> : <UploadCloud size={16} />}
                上传猫视频 / 背景图 / 文案
                <input type="file" accept="video/*,image/*,.txt,.md,.json" multiple onChange={uploadMaterials} />
              </label>
              {materialUploads.length > 0 && (
                <div className="uploadList">
                  {materialUploads.slice(0, 4).map((item) => <small key={item.upload_id}>{kindLabel(item.kind)} · {item.filename}</small>)}
                </div>
              )}
            </div>
            <div className="controlRow">
              <span>时长</span>
              <div className="segmented compact" aria-label="视频时长">
                {durationOptions.map((option) => (
                  <button
                    className={durationMode === option.value ? 'active' : ''}
                    key={option.value}
                    onClick={() => setDurationMode(option.value)}
                    type="button"
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="controlRow">
              <span>模式</span>
              <div className="segmented compact" aria-label="生成模式">
                {generationModes.map((option) => (
                  <button
                    className={generationMode === option.value ? 'active' : ''}
                    key={option.value}
                    onClick={() => setGenerationMode(option.value)}
                    type="button"
                  >
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <button className="wide" onClick={generateCandidates} disabled={loading === 'candidates'}>
              {loading === 'candidates' ? <Loader2 className="spin" size={18} /> : <Wand2 size={18} />}
              生成 3 个剧本候选
            </button>
          </div>
          {streamStatus && (
            <div className="streamBox">
              <strong>{streamStatus.message}</strong>
              <div className="progress"><span style={{ width: `${Math.round((streamStatus.progress || 0) * 100)}%` }} /></div>
            </div>
          )}
          {error && <div className="error"><AlertTriangle size={18} />{error}</div>}
        </aside>

        <section className="panel center">
          <div className="sectionTitle"><Film size={20} /><h2>候选剧本</h2></div>
          <div className="candidateGrid">
            {candidateDisplayList.map((candidate) => (
              <article className={`candidate ${selectedCandidate?.id === candidate.id ? 'selected' : ''}`} key={candidate.id}>
                <div className="candidateHead">
                  <h3>{candidate.title}</h3>
                  <span>{candidate.streaming ? '...' : candidate.score}</span>
                </div>
                <p>{candidate.tension || candidate.social_topic || '猫 meme 反差剧本'}</p>
                <small>预计 {expectedDuration(candidate.script)} 秒 · {candidate.script.length} 个镜头</small>
                {migrationLabel(candidate) && <small>{migrationLabel(candidate)}</small>}
                {candidate.user_material_coverage?.available && (
                  <small>用户素材：猫 {candidate.user_material_coverage.motion_count || 0} / 背景 {candidate.user_material_coverage.background_count || 0}</small>
                )}
                {publicCandidateNote(candidate.notes) && <small>{publicCandidateNote(candidate.notes)}</small>}
                <ul>
                  {candidate.script.map((item, index) => <li key={`${candidate.id}-${index}`}>{item.text}</li>)}
                </ul>
                <button onClick={() => selectCandidate(candidate)} disabled={candidate.streaming || loading === `select-${candidate.id}`}>
                  {loading === `select-${candidate.id}` ? <Loader2 className="spin" size={18} /> : <CheckCircle2 size={18} />}
                  {candidate.streaming ? '生成中' : '选择此剧本'}
                </button>
              </article>
            ))}
            {loading === 'candidates' && candidateDisplayList.length === 0 && (
              <div className="streamingSkeleton">
                <Loader2 className="spin" size={18} />
                <span>编剧 Agent 正在组织第一个候选...</span>
              </div>
            )}
          </div>

          <div className="sectionTitle later"><RefreshCw size={20} /><h2>自然语言调整</h2></div>
          {suggestions.length > 0 && (
            <div className="suggestionRow">
              {suggestions.map((item) => (
                <button type="button" key={item} onClick={() => setInstruction(item)}>{item}</button>
              ))}
            </div>
          )}
          <div className="reviseRow">
            <input value={instruction} onChange={(event) => setInstruction(event.target.value)} />
            <button onClick={revisePlan} disabled={(!plan && !selectedCandidate && candidates.length === 0) || loading === 'revise'}>
              {loading === 'revise' ? <Loader2 className="spin" size={18} /> : <RefreshCw size={18} />}
              调整
            </button>
          </div>
          <small className="reviseHint">
            {plan ? '将调整当前分镜，调整后需要重新生成视频。' : '未选择剧本时，将默认调整第一条候选并生成分镜。'}
          </small>

          <section className="resultPanel">
            <div className="sectionTitle"><Play size={20} /><h2>视频预览</h2></div>
            {videoUrl ? (
              <div className="videoBox">
                <video src={videoUrl} controls playsInline />
                <div className="videoActions">
                  <a className="downloadButton" href={videoUrl} download={videoName}>
                    <Download size={18} />
                    下载视频
                  </a>
                  <span>{videoName}</span>
                </div>
              </div>
            ) : (
              <div className="emptyState">
                {plan ? '当前分镜还没有生成视频。调整完成后点击“生成视频”即可预览和下载。' : '选择剧本后会在这里生成视频预览。'}
              </div>
            )}
          </section>
        </section>

        <aside className="panel right">
          <div className="sectionTitle"><Film size={20} /><h2>分镜时间线</h2></div>
          {storyboardStatus && (
            <div className="streamBox compact">
              <strong>{storyboardStatus.message}</strong>
              <div className="progress"><span style={{ width: `${Math.round((storyboardStatus.progress || 0) * 100)}%` }} /></div>
            </div>
          )}
          <div className="timeline">
            {timeline.map((slot) => (
              <article className="slot" key={slot.id}>
                <div className="slotTime">{slot.start.toFixed(1)}-{slot.end.toFixed(1)}s</div>
                <div className="slotBody">
                  <div className="slotHead">
                    <h3>{slot.copy}</h3>
                    <StatusPill type={slot.gap?.status === 'matched' ? 'ok' : 'filled'} />
                  </div>
                  <p>{shortSlotText(slot.motion.description, 54)}</p>
                  <div className="slotMeta">
                    <span>裁剪 {formatClip(slot.motion_clip)}</span>
                    {slot.secondary_motion_clip && <span>右猫 {formatClip(slot.secondary_motion_clip)}</span>}
                    <span>转场 {transitionLabel(slot.transition)}</span>
                    <span>{sourceLabel(slot.asset_sources?.motion || 'built_in')}猫</span>
                    <span>{sourceLabel(slot.asset_sources?.structure || 'theme_workflow')}</span>
                    {viralShotLabel(slot.source_viral_shot) && <span>{viralShotLabel(slot.source_viral_shot)}</span>}
                    <span className={slot.background_source === 'generated' ? 'generated' : ''}>
                      背景 {slot.background_source === 'generated' ? 'Seedream' : sourceLabel(slot.asset_sources?.background || 'built_in')}
                    </span>
                  </div>
                  {slot.layout === 'dialogue' && slot.dialogue?.length > 0 && (
                    <div className="dialoguePreview">
                      {slot.dialogue.map((line, index) => (
                        <span key={`${slot.id}-dialogue-${index}`}>{line.text}</span>
                      ))}
                    </div>
                  )}
                  {slot.overlay_actions?.length > 0 && (
                    <div className="overlayPreview">
                      {slot.overlay_actions.map((action, index) => (
                        <span key={`${slot.id}-overlay-${index}`}>{overlayLabel(action)}</span>
                      ))}
                    </div>
                  )}
                  <details className="slotDetails">
                    <summary>背景与质检</summary>
                    <small>{slot.background.description} · {slot.gap?.strategy}</small>
                  </details>
                  {slot.background_source === 'generated' && slot.background_prompt && (
                    <small>补图：{slot.background_prompt}</small>
                  )}
                </div>
              </article>
            ))}
          </div>
          {job && (
            <div className="jobBox">
              <strong>{job.message}</strong>
              <div className="progress"><span style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} /></div>
              {videoUrl && <small>视频已生成，可在中间区域预览或下载。</small>}
              {job.fallback_reason && <small>{friendlyFallback(job.fallback_reason)}</small>}
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}

const durationOptions = [
  { value: 'short', label: '短版' },
  { value: 'medium', label: '30秒' },
  { value: 'minute', label: '1分钟' }
];

const generationModes = [
  { value: 'agent', label: 'Agent 自主' },
  { value: 'workflow', label: 'Workflow 稳定' }
];

const briefSuggestionFields = [
  { key: 'target_audience', label: '受众' },
  { key: 'protagonist', label: '主角' },
  { key: 'core_conflict', label: '冲突' },
  { key: 'ending_tone', label: '结尾' },
  { key: 'required_scenes', label: '场景' },
  { key: 'required_props', label: '道具' }
];

function modePayload(mode) {
  return {
    generation_mode: mode,
    use_doubao: mode === 'agent'
  };
}

function defaultCreativeBrief() {
  return {
    viral_topic: '',
    target_audience: '大学生和刚上班的年轻人',
    protagonist: '一只普通但嘴硬的打工猫',
    core_conflict: '',
    ending_tone: '讽刺但留一点温暖',
    style: '社会现实黑色幽默',
    required_scenes: '',
    required_props: '',
    avoid_content: '',
    main_cat_count: '1-2只主角猫',
    allow_multi_cat: true,
    allow_ai_fill: false
  };
}

function AnalysisCard({ job }) {
  const summary = job?.summary || {};
  return (
    <div className="analysisCard">
      <div className="uploadHead">
        <strong>{job.status === 'done' ? '分析完成' : job.message}</strong>
        <span>{Math.round((job.progress || 0) * 100)}%</span>
      </div>
      <div className="progress"><span style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} /></div>
      {job.status === 'done' && (
        <>
          <small>{summary.one_sentence || summary.title || '已抽取爆款结构'}</small>
          <small>{summary.shot_count || 0} 个分镜 · {summary.audio_style || '声音风格待复核'}</small>
        </>
      )}
      {job.status === 'error' && <small>{job.error || '分析失败'}</small>}
    </div>
  );
}

function BriefSuggestionPanel({ suggestions = {}, provider = '', onPick }) {
  const entries = briefSuggestionFields
    .map((field) => ({ ...field, values: suggestions[field.key] || [] }))
    .filter((field) => field.values.length);
  if (!entries.length) return null;
  return (
    <div className="briefSuggestions">
      <div className="miniHead">
        <strong>补全建议</strong>
        <span>{provider === 'mini' ? 'mini' : 'fallback'}</span>
      </div>
      {entries.map((field) => (
        <div className="suggestionGroup" key={field.key}>
          <small>{field.label}</small>
          <div>
            {field.values.map((value) => (
              <button type="button" key={`${field.key}-${value}`} onClick={() => onPick(field.key, value)}>
                {value}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function mergeUploads(current = [], next = []) {
  const byId = new Map(current.map((item) => [item.upload_id, item]));
  next.forEach((item) => byId.set(item.upload_id, item));
  return Array.from(byId.values());
}

function formatBytes(value = 0) {
  const bytes = Number(value || 0);
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function kindLabel(kind = '') {
  const labels = {
    viral_video: '爆款',
    user_cat_motion: '猫视频',
    user_background: '背景',
    user_text: '文案'
  };
  return labels[kind] || kind;
}

function sourceLabel(value = '') {
  const labels = {
    user_upload: '用户素材',
    built_in: '内置素材',
    generated: 'AI补足',
    uploaded_viral: '上传爆款迁移',
    viral_library: '爆款库迁移',
    theme_workflow: '主题生成'
  };
  return labels[value] || value;
}

function migrationLabel(candidate = {}) {
  const ref = candidate.source_reference || {};
  if (!ref.title) return '';
  const tags = Array.isArray(ref.structure_tags) ? ref.structure_tags.filter(Boolean).slice(0, 2).join(' / ') : '';
  const support = Array.isArray(ref.supporting) && ref.supporting.length ? ` +${ref.supporting.length} 辅助结构` : '';
  return `迁移自：${ref.title}${tags ? ` · ${tags}` : ''}${support}`;
}

function viralShotLabel(source = {}) {
  if (!source?.viral_title) return '';
  const shot = source.shot_id ? ` #${source.shot_id}` : '';
  return `爆款镜头 ${source.viral_title}${shot}`;
}

function expectedDuration(script = []) {
  return Math.round(script.reduce((total, item) => total + Number(item.duration || 0), 0));
}

function StatusPill({ type }) {
  const labels = { ok: '素材命中', filled: '包装补全', copy: '字幕补全' };
  return <span className={`pill ${type}`}>{labels[type] || type}</span>;
}

function overlayLabel(action) {
  const labels = {
    throw_object: '飞物件',
    stamp_reject: '盖章',
    popup: '弹窗',
    impact_burst: '爆字',
    phone_job_feed: '手机',
    job_requirement_card: '岗位卡',
    work_chat_stack: '工作群',
    chat_stack: '聊天框',
    choice_panel: '选择框',
    study_card: '复习卡',
    bill_card: '账单',
    commute_card: '通勤卡',
    stall_sign: '摊位牌',
    leave_request: '请假审批',
    emergency_call: '120',
    generated_sticker: '贴纸'
  };
  return `${labels[action.type] || action.type}：${action.text || action.title || action.object || ''}`;
}

function formatClip(clip = {}) {
  const start = Number(clip.start || 0).toFixed(1);
  const duration = Number(clip.duration || 0).toFixed(1);
  return `${start}s + ${duration}s${clip.loop ? ' 循环' : ''}`;
}

function transitionLabel(transition = {}) {
  const labels = { cut: '直切', fade: '淡入淡出', whip: '甩切', zoom: '推近', flash: '白闪' };
  return labels[transition.type] || transition.type || '直切';
}

function shortSlotText(text = '', limit = 58) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  return value.length <= limit ? value : `${value.slice(0, limit)}...`;
}

function upsertById(items = [], item) {
  const id = item?.id;
  if (!id) return items;
  const exists = items.some((entry) => entry.id === id);
  return exists ? items.map((entry) => (entry.id === id ? item : entry)) : [...items, item];
}

function upsertTimelineById(items = [], item) {
  return upsertById(items, item).sort((a, b) => Number(a.start || 0) - Number(b.start || 0));
}

function displayCandidates({ loading, candidates = [], draftCandidates = [] }) {
  if (loading === 'candidates') {
    let merged = normalizeCandidateList(draftCandidates, { fillPlaceholders: true });
    for (const candidate of normalizeCandidateList(candidates)) {
      const index = candidatePosition(candidate);
      if (index >= 0 && index < 3) {
        merged[index] = candidate;
      } else {
        const emptyIndex = merged.findIndex((item) => item?.streaming && String(item.id || '').startsWith('draft-'));
        if (emptyIndex >= 0) {
          merged[emptyIndex] = candidate;
        }
      }
    }
    return normalizeCandidateList(merged, { fillPlaceholders: true });
  }
  return normalizeCandidateList(candidates);
}

function normalizeCandidateList(items = [], options = {}) {
  const placeholders = options.fillPlaceholders ? streamingCandidatePlaceholders() : [];
  const next = options.fillPlaceholders ? [...placeholders] : [];
  const seen = new Set();
  for (const item of items) {
    if (!item?.id) continue;
    const index = candidatePosition(item);
    const normalized = sanitizeCandidate(item);
    if (index >= 0 && index < 3) {
      next[index] = { ...(next[index] || placeholders[index]), ...normalized };
      seen.add(next[index].id);
      continue;
    }
    if (next.length < 3 && !seen.has(normalized.id)) {
      next.push(normalized);
      seen.add(normalized.id);
    }
  }
  return next.slice(0, 3).map((item, index) => sanitizeCandidate(item || placeholders[index] || streamingCandidatePlaceholders()[index]));
}

function replaceCandidateByPosition(items = [], item) {
  const base = normalizeCandidateList(items, { fillPlaceholders: true });
  const index = candidatePosition(item);
  if (index >= 0 && index < 3) {
    base[index] = { ...base[index], ...item };
    return normalizeCandidateList(base, { fillPlaceholders: true });
  }
  return normalizeCandidateList(upsertById(base, item), { fillPlaceholders: true });
}

function candidatePosition(candidate = {}) {
  const id = String(candidate.id || '');
  const match = id.match(/(?:candidate|draft)-(\d+)$/);
  if (match) return Number(match[1]) - 1;
  const titleMatch = String(candidate.title || '').match(/候选\s*(\d+)/);
  if (titleMatch) return Number(titleMatch[1]) - 1;
  return -1;
}

function sanitizeCandidate(candidate = {}) {
  return {
    ...candidate,
    streaming: Boolean(candidate.streaming),
    script: Array.isArray(candidate.script) ? candidate.script : [],
    notes: publicCandidateNotes(candidate.notes || [])
  };
}

function publicCandidateNotes(notes = []) {
  return notes.filter((note) => {
    const text = String(note || '');
    if (!text.trim()) return false;
    return ![
      '生成来源',
      'doubao',
      'Doubao',
      'Agent',
      '文本素材库',
      '爆款',
      'viral',
      '等待真实',
      '草稿预览'
    ].some((keyword) => text.includes(keyword));
  });
}

function publicCandidateNote(notes = []) {
  return publicCandidateNotes(notes)[0] || '';
}

function normalizeSlot(slot) {
  return {
    ...slot,
    copy: slot.copy || slot.caption || slot.intent || slot.role,
    gap: slot.gap || { status: 'matched', strategy: 'direct_match' },
    motion_clip: slot.motion_clip || { start: 0, duration: 4 },
    transition: slot.transition || { type: 'cut', duration: 0 }
  };
}

function friendlyFallback(reason = '') {
  if (reason.includes('hyperframes_runtime_unavailable')) return '已使用稳定 FFmpeg/Pillow 渲染';
  return reason;
}

function streamingCandidatePlaceholders() {
  return [1, 2, 3].map((index) => ({
    id: `draft-${index}`,
    title: `候选 ${index} 正在生成`,
    social_topic: '正在组织现实矛盾和猫 meme 反差',
    tension: '',
    score: '',
    script: [{ text: '等待第一段字幕...' }],
    notes: [],
    streaming: true
  }));
}

function mergeDraftCandidates(current = [], drafts = []) {
  if (!drafts.length) return current;
  const next = [...current];
  drafts.forEach((draft, index) => {
    next[index] = { ...(next[index] || streamingCandidatePlaceholders()[index]), ...draft, streaming: true };
  });
  return next;
}

function extractCandidateDrafts(raw = '') {
  const drafts = [];
  const titlePattern = /"name"\s*:\s*"([^"]+)"/g;
  let match;
  while ((match = titlePattern.exec(raw)) && drafts.length < 3) {
    drafts.push({
      id: `draft-${drafts.length + 1}`,
      title: match[1],
      script: []
    });
  }

  const beatTexts = [...raw.matchAll(/\[\s*"[^"]+"\s*,\s*"([^"]+)"/g)].map((item) => item[1]);
  if (beatTexts.length) {
    const perCandidate = Math.max(1, Math.ceil(beatTexts.length / Math.max(1, drafts.length || 3)));
    for (let index = 0; index < Math.max(drafts.length, Math.min(3, Math.ceil(beatTexts.length / perCandidate))); index += 1) {
      const slice = beatTexts.slice(index * perCandidate, index * perCandidate + perCandidate).slice(0, 8);
      drafts[index] = {
        ...(drafts[index] || { id: `draft-${index + 1}`, title: `候选 ${index + 1} 正在生成` }),
        script: slice.map((text) => ({ text }))
      };
    }
  }
  return drafts;
}

createRoot(document.getElementById('root')).render(<App />);
