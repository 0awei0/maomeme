import React, { useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AlertTriangle, CheckCircle2, Clapperboard, Download, Film, Loader2, Play, RefreshCw, Wand2 } from 'lucide-react';
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
  const candidateRunRef = useRef(0);

  const timeline = plan?.timeline || [];
  const videoUrl = job?.video_url ? `${API_BASE}${job.video_url}` : '';
  const videoName = job?.output_path?.split('/').pop() || 'maomeme-video.mp4';
  const canRender = plan && job?.status !== 'running' && loading !== 'render';
  const candidateDisplayList = useMemo(
    () => displayCandidates({ loading, candidates, draftCandidates }),
    [loading, candidates, draftCandidates]
  );

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
        duration_mode: durationMode
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
        duration_mode: durationMode
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
        duration_mode: durationMode
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
        duration_mode: durationMode
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
        allow_ai_fill: false
      });
      setJob(data.job);
      pollJob(data.job.job_id);
    } catch (err) {
      setError(err.message);
      setLoading('');
    }
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
          <h1>猫 meme 爆款结构迁移引擎</h1>
          <p>输入社会现实主题，Agent 生成 3 个剧本候选，选择后匹配猫动画和背景，最后合成带字幕和原声的视频。</p>
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
            <div className="segmented" aria-label="视频时长">
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
            <div className="segmented" aria-label="生成模式">
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
                  <p>{slot.motion.description}</p>
                  <div className="slotMeta">
                    <span>裁剪 {formatClip(slot.motion_clip)}</span>
                    {slot.secondary_motion_clip && <span>右猫 {formatClip(slot.secondary_motion_clip)}</span>}
                    <span>转场 {transitionLabel(slot.transition)}</span>
                    <span className={slot.background_source === 'generated' ? 'generated' : ''}>
                      背景 {slot.background_source === 'generated' ? 'Seedream' : '现有'}
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
                  <small>{slot.background.description} · {slot.gap?.strategy}</small>
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

function modePayload(mode) {
  return {
    generation_mode: mode,
    use_doubao: mode === 'agent'
  };
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
    generated_sticker: '贴纸'
  };
  return `${labels[action.type] || action.type}：${action.text || action.object || ''}`;
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
        const emptyIndex = merged.findIndex((item) => item.streaming && String(item.id || '').startsWith('draft-'));
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
  return next.slice(0, 3).map(sanitizeCandidate);
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
