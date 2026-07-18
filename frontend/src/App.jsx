import { useEffect, useRef, useState } from 'react'
import {
  AppShell, Badge, Box, Button, Card, Group, HoverCard, Image, Loader,
  Collapse, MultiSelect, Paper, ScrollArea, SegmentedControl, Select, Stack, Text, Textarea, Title, Tooltip, rem, Modal, FileInput,
} from '@mantine/core'
import {
  IconMaximize, IconMessage, IconMovie, IconPhoto, IconRefresh, IconScan, IconSparkles, IconVolume, IconWaveSine, IconUpload,
} from '@tabler/icons-react'
import { api, useAdDetection, useVideos } from './hooks/useAdDetection.js'

export default function App() {
  const { videos, reload: load } = useVideos()
  const [selected, setSelected] = useState(null)
  const [view, setView] = useState('videos')
  const [resume, setResume] = useState(null)
  const [uploadModal, setUploadModal] = useState(false)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)

  const handleUpload = async () => {
    if (!uploadFile) return
    setUploading(true)
    setUploadError(null)
    try {
      const body = new FormData()
      body.append('video', uploadFile)
      const res = await fetch('/api/upload', { method: 'POST', body }).then((r) => r.json())
      if (res.error) { setUploadError(res.error); return }
      setUploadFile(null)
      setUploadModal(false)
      await load()
    } catch (err) {
      setUploadError(err.message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <AppShell navbar={{ width: 240, breakpoint: 0 }} padding="md">
      <AppShell.Navbar p="md">
        <Group justify="space-between" mb="md">
          <Group gap={8}>
            <IconMovie size={20} />
            <Title order={5}>Ad Insertion</Title>
          </Group>
          <Group gap={4}>
            <Button variant="subtle" size="compact-xs" onClick={() => setUploadModal(true)} title="Upload video">
              <IconUpload size={14} />
            </Button>
            <Button variant="subtle" size="compact-xs" onClick={load}>
              <IconRefresh size={14} />
            </Button>
          </Group>
        </Group>
        <SegmentedControl
          fullWidth size="xs" mb="sm" value={view} onChange={setView}
          data={[{ value: 'videos', label: 'Videos' }, { value: 'sessions', label: 'Sessions' }]}
        />
        <ScrollArea>
          <Stack gap="xs">
            {videos.map((v) => (
              <Card
                key={v.video_id}
                padding="sm"
                radius="md"
                withBorder
                style={{ cursor: 'pointer', borderColor: selected?.video_id === v.video_id ? 'var(--mantine-color-indigo-5)' : undefined }}
                onClick={() => setSelected(v)}
              >
                <Text size="xs" ff="monospace" lineClamp={2}>{v.filename}</Text>
                <Group gap={6} mt={6}>
                  <Badge size="xs" color={v.analyzed ? 'teal' : 'yellow'} variant="light">
                    {v.analyzed ? 'analyzed' : 'not analyzed'}
                  </Badge>
                  {v.edited && <Badge size="xs" color="indigo" variant="light">processed</Badge>}
                </Group>
              </Card>
            ))}
            {videos.length === 0 && <Text size="sm" c="dimmed" ta="center" mt="xl">No videos</Text>}
          </Stack>
        </ScrollArea>
      </AppShell.Navbar>

      <AppShell.Main>
        {view === 'sessions'
          ? <SessionsDashboard onContinue={(s) => {
              const v = videos.find((x) => x.filename === s.filename)
              if (v) { setSelected(v); setResume(s); setView('videos') }
            }} />
          : selected
            ? <VideoDetail video={selected} resume={resume} key={selected.video_id + (resume?.id || '')} />
            : <Text c="dimmed" ta="center" mt="30vh">Select a video</Text>}
      </AppShell.Main>

      <Modal opened={uploadModal} onClose={() => setUploadModal(false)} title="Upload video" size="md">
        <Stack gap="md">
          <FileInput
            label="Video file" placeholder="Choose a video" accept="video/*"
            value={uploadFile} onChange={setUploadFile}
          />
          {uploadError && <Text size="xs" c="red">{uploadError}</Text>}
          <Group justify="flex-end" gap="sm">
            <Button variant="default" size="xs" onClick={() => setUploadModal(false)}>Cancel</Button>
            <Button size="xs" onClick={handleUpload} loading={uploading} disabled={!uploadFile}>Upload</Button>
          </Group>
        </Stack>
      </Modal>
    </AppShell>
  )
}

function VideoDetail({ video, resume }) {
  const { analysis, job, detect } = useAdDetection(video)
  const videoRef = useRef(null)

  const seek = (t) => {
    if (videoRef.current) {
      videoRef.current.currentTime = t
      videoRef.current.pause()
    }
  }

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text ff="monospace" size="sm" fw={600} lineClamp={1} style={{ flex: 1 }}>{video.filename}</Text>
        <Button
          size="xs"
          leftSection={job ? <Loader size={12} color="white" /> : <IconScan size={14} />}
          onClick={detect}
          disabled={!!job}
        >
          {job ? (job.progress || 'running…') : analysis ? 'Re-detect' : 'Detect placements'}
        </Button>
      </Group>

      <Group align="flex-start" gap="md" wrap="nowrap">
        <Player src={video.original} videoRef={videoRef} />
        {analysis && (
          <Paper p="sm" radius="md" withBorder style={{ flex: 1 }}>
            <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb={6}>Summary</Text>
            <Stack gap={4}>
              <Group gap={8}><IconPhoto size={14} /><Text size="sm">{analysis.visual_slots.length} visual placements</Text></Group>
              <Group gap={8}><IconVolume size={14} /><Text size="sm">{analysis.audio_slots.length} audio gaps</Text></Group>
              <Text size="xs" c="dimmed">{Math.round(analysis.duration)}s duration</Text>
            </Stack>
          </Paper>
        )}
      </Group>

      {analysis && <Timeline analysis={analysis} onSeek={seek} />}
      {analysis && <AudioBranding video={video} analysis={analysis} resume={resume} />}
      {!analysis && !job && <Text size="sm" c="dimmed">Run detection to see ad placement opportunities.</Text>}
    </Stack>
  )
}

function AudioBranding({ video, analysis, resume }) {
  const [mode, setMode] = useState('visual')
  const [visualIdx, setVisualIdx] = useState(null)
  const [vQuality, setVQuality] = useState('draft')
  const [brands, setBrands] = useState([])
  const [brandSel, setBrandSel] = useState([])
  const [gap, setGap] = useState(null)
  const [swapIdx, setSwapIdx] = useState(null)
  const [context, setContext] = useState('')
  const [busy, setBusy] = useState(false)
  // resuming a session preloads its edit stack and appends to it server-side
  const [results, setResults] = useState(() =>
    (resume?.edits || []).map((e, i) => ({ ...e.detail, kind: e.kind, key: `${resume.id}-${i}` })))
  const [sessionId, setSessionId] = useState(resume?.id || null)
  const [error, setError] = useState(null)
  // edits stack automatically: first edit starts from the original,
  // every subsequent edit chains on top of the previous result
  const chain = results.length > 0 || !!sessionId

  // audience targeting: reorders the brand pickers by demographic fit and
  // is forwarded to the backend so swap/script generation is on-target too
  const [audiences, setAudiences] = useState([])
  const [audienceId, setAudienceId] = useState(null)

  useEffect(() => { api('/api/audiences').then(setAudiences) }, [])
  useEffect(() => {
    api(audienceId ? `/api/brands?audience=${audienceId}` : '/api/brands').then(setBrands)
  }, [audienceId])

  // brand options carry the fit score once a segment is selected
  const brandOptions = brands.map((b) => ({
    value: b.name,
    label: b.audience_score !== undefined
      ? `${b.name} — fit ${Math.round(b.audience_score * 100)}%`
      : b.name,
  }))

  const [swapBrand, setSwapBrand] = useState(null)
  const [rescanning, setRescanning] = useState(false)
  const [liveSwaps, setLiveSwaps] = useState(analysis.dialogue_swaps || [])
  const swaps = liveSwaps

  const gaps = analysis.audio_slots.map((s, i) => ({
    value: String(i),
    label: `${s.start_ts}s → ${s.end_ts}s  (${s.duration}s)`,
  }))

  const sceneDefault = (analysis.integrations || []).map((x) => x.description).join('; ')

  const generate = async () => {
    setBusy(true); setError(null)
    let res
    if (mode === 'visual') {
      res = await fetch(brandSel.length > 1 ? '/api/place_variants' : '/api/place_visual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          slot_index: +visualIdx,
          brand: brandSel[0],
          brands: brandSel.length > 1 ? brandSel : undefined,
          kind: 'visual',
          quality: vQuality,
          chain,
          session_id: sessionId,
        }),
      }).then((r) => r.json())
    } else if (mode === 'dialogue') {
      res = await fetch('/api/place_dialogue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          swap_index: +swapIdx,
          chain,
          session_id: sessionId,
        }),
      }).then((r) => r.json())
    } else {
      const slot = analysis.audio_slots[+gap]
      res = await fetch(brandSel.length > 1 ? '/api/place_variants' : '/api/place_audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          brand: brandSel[0],
          brands: brandSel.length > 1 ? brandSel : undefined,
          kind: 'gap_spot',
          start_ts: slot.start_ts,
          gap_duration: slot.duration,
          scene_context: context || sceneDefault,
          audience: audienceId,
          chain,
          session_id: sessionId,
        }),
      }).then((r) => r.json())
    }
    setBusy(false)
    if (res.error) { setError(res.error); return }
    if (res.variants) {
      // multi-brand: each variant lives in its OWN session (not chained)
      setResults((prev) => [...prev,
        ...res.variants.map((v, i) => ({ ...v, key: Date.now() + i }))])
      return
    }
    if (res.session_id) setSessionId(res.session_id)
    setResults((prev) => [...prev, { ...res, key: Date.now() }])
  }

  const latest = results[results.length - 1]

  // ---- user-directed placement -------------------------------------------
  // The user says where the ad goes and what changes; the backend resolves it
  // to a real slot (or offers nearest alternatives) which we then place.
  const [directive, setDirective] = useState('')
  const [dirBusy, setDirBusy] = useState(false)
  const [dirResult, setDirResult] = useState(null)
  // chat is the primary path; the old per-field controls stay one click away
  // for anything the chat can't express or when a parse comes back wrong
  const [showManual, setShowManual] = useState(false)

  const runDirective = async () => {
    if (!directive.trim()) return
    setDirBusy(true); setError(null); setDirResult(null)
    try {
      const res = await fetch('/api/directive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename, request: directive, audience: audienceId,
        }),
      }).then((r) => r.json())
      if (res.error) { setError(res.error); return }
      setDirResult(res)
    } catch (err) {
      // network/backend down — otherwise the button spins forever
      setError(`Could not reach the server: ${err.message}`)
    } finally {
      setDirBusy(false)
    }
  }

  // Render every variant the request asked for, each in its own session.
  const placeVariants = async (r) => {
    setBusy(true); setError(null)
    try {
      const res = await fetch('/api/place_directive', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          resolved: r,
          variants: dirResult.intent.variants,
          quality: vQuality,
          scene_context: dirResult.intent.instruction || sceneDefault,
        }),
      }).then((x) => x.json())
      if (res.error) { setError(res.error); return }
      const ok = (res.variants || []).filter((v) => !v.error)
      const failed = (res.variants || []).filter((v) => v.error)
      if (failed.length) {
        setError(`${failed.length} of ${res.variants.length} variants failed: ${failed[0].error}`)
      }
      setResults((prev) => [...prev, ...ok.map((v, i) => ({ ...v, key: Date.now() + i }))])
      if (ok.length) { setDirResult(null); setDirective('') }
    } catch (err) {
      setError(`Could not reach the server: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  // Execute a resolved (or fallback) slot through the normal placement APIs.
  const placeResolved = async (r, opts = {}) => {
    setBusy(true); setError(null)
    const chosenBrand = dirResult?.intent?.brand || brandSel[0]
    const common = { filename: video.filename, chain, session_id: sessionId }
    let res
    try {
    if (r.kind === 'visual') {
      res = await fetch('/api/place_visual', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...common, slot_index: r.slot_index, brand: chosenBrand, quality: vQuality }),
      }).then((x) => x.json())
    } else if (r.kind === 'audio') {
      res = await fetch('/api/place_audio', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...common, brand: chosenBrand, start_ts: r.start_ts,
          gap_duration: r.gap_duration, audience: audienceId,
          scene_context: dirResult?.intent?.instruction || context || sceneDefault,
        }),
      }).then((x) => x.json())
    } else {
      res = await fetch('/api/place_dialogue', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...common, swap_index: opts.swapIndex ?? r.swap_index }),
      }).then((x) => x.json())
    }
    if (res.error) { setError(res.error); return }
    if (res.session_id) setSessionId(res.session_id)
    setResults((prev) => [...prev, { ...res, key: Date.now() }])
    setDirResult(null); setDirective('')
    } catch (err) {
      setError(`Could not reach the server: ${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  const startSession = async () => {
    const s = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: video.filename }),
    }).then((r) => r.json())
    if (s.id) setSessionId(s.id)
  }

  if (!sessionId) {
    return (
      <Paper p="md" radius="md" withBorder>
        <Group justify="space-between">
          <div>
            <Text size="xs" c="dimmed" tt="uppercase" fw={700}>Ad Integration</Text>
            <Text size="xs" c="dimmed" mt={4}>All edits happen inside a session — start one to begin, or continue an existing session from the Sessions tab.</Text>
          </div>
          <Button size="xs" onClick={startSession}>Start ad session</Button>
        </Group>
      </Paper>
    )
  }

  return (
    <Paper p="md" radius="md" withBorder>
      <Group justify="space-between" mb="sm">
        <Group gap={8}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>Ad Integration</Text>
          <Badge size="xs" variant="outline" color="gray" ff="monospace">session {sessionId}</Badge>
        </Group>
        <Button size="compact-xs" variant="subtle" onClick={() => setShowManual((v) => !v)}>
          {showManual ? 'Hide manual controls' : 'Manual controls'}
        </Button>
      </Group>

      <Paper p="sm" radius="md" mb="sm" withBorder bg="var(--mantine-color-dark-8)">
        <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb={6}>Describe the ad you want</Text>
        <Group align="flex-start" gap="sm" wrap="nowrap">
          <Textarea
            placeholder={'e.g. "put a Coke billboard on the back wall at 0:45"\n'
              + '"3 different drink brands for 3 different regions on the podium at 0:08"\n'
              + '"change the line about coffee to mention Starbucks, aimed at young professionals"'}
            size="xs" autosize minRows={2} style={{ flex: 1 }}
            value={directive}
            onChange={(e) => setDirective(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); runDirective() }
            }}
          />
          <Button size="xs" onClick={runDirective} loading={dirBusy} disabled={!directive.trim()}>
            Interpret
          </Button>
        </Group>

        {dirResult && (
          <Stack gap={6} mt="sm">
            <Group gap={6} wrap="wrap">
              <Badge size="xs" variant="light">{dirResult.intent.kind}</Badge>
              {(dirResult.intent.variants || []).length > 1 && (
                <Badge size="xs" variant="filled" color="grape">
                  {dirResult.intent.variants.length} variants
                </Badge>
              )}
              {dirResult.intent.start_ts !== null && dirResult.intent.start_ts !== undefined && (
                <Badge size="xs" variant="light" color="cyan">
                  @ {dirResult.intent.start_ts}s ({dirResult.intent.time_source})
                </Badge>
              )}
              {dirResult.intent.target && <Badge size="xs" variant="outline" color="gray">{dirResult.intent.target}</Badge>}
            </Group>
            <Text size="xs" c="dimmed">{dirResult.intent.instruction}</Text>

            {(dirResult.intent.variants || []).length > 0 && (
              <Stack gap={2}>
                {dirResult.intent.variants.map((v, i) => (
                  <Group key={i} gap={6} wrap="wrap">
                    <Badge size="xs" variant="light" color="grape">{v.brand}</Badge>
                    {v.region && <Badge size="xs" variant="outline" color="blue">{v.region}</Badge>}
                    {v.audience && <Badge size="xs" variant="outline" color="teal">{v.audience}</Badge>}
                    {v.why && <Text size="xs" c="dimmed">{v.why}</Text>}
                  </Group>
                ))}
              </Stack>
            )}

            {dirResult.needs_clarification && (
              <Text size="xs" c="yellow">{dirResult.intent.clarification}</Text>
            )}

            {dirResult.note && <Text size="xs" c="orange">{dirResult.note}</Text>}

            {dirResult.resolved?.kind === 'dialogue' ? (
              <Stack gap={4}>
                {dirResult.resolved.options.map((s, i) => (
                  <Group key={i} gap="sm" wrap="nowrap">
                    <Text size="xs" style={{ flex: 1 }}>
                      [{s.brand}] "{s.full_line_after}" @ {s.start_ts}s
                    </Text>
                    <Button size="compact-xs" loading={busy}
                      onClick={() => placeResolved(dirResult.resolved, { swapIndex: dirResult.resolved.swap_index + i })}>
                      Apply
                    </Button>
                  </Group>
                ))}
              </Stack>
            ) : dirResult.resolved ? (
              <Group gap="sm">
                <Text size="xs" c="teal" style={{ flex: 1 }}>
                  Found: {dirResult.resolved.slot.surface || 'quiet gap'} @{' '}
                  {dirResult.resolved.slot.timestamp ?? dirResult.resolved.start_ts}s
                </Text>
                <Button size="compact-xs" loading={busy}
                  disabled={!(dirResult.intent.variants?.length || brandSel[0])}
                  onClick={() => ((dirResult.intent.variants || []).length > 1
                    ? placeVariants(dirResult.resolved)
                    : placeResolved(dirResult.resolved))}>
                  {(dirResult.intent.variants || []).length > 1
                    ? `Generate ${dirResult.intent.variants.length} ads`
                    : 'Place it'}
                </Button>
              </Group>
            ) : null}

            {dirResult.fallbacks?.length > 0 && (
              <Stack gap={4}>
                <Text size="xs" c="dimmed">Nearest alternatives:</Text>
                {dirResult.fallbacks.map((f, i) => (
                  <Group key={i} gap="sm" wrap="nowrap">
                    <Text size="xs" style={{ flex: 1 }}>
                      {f.slot.surface || f.slot.full_line_after || 'quiet gap'} @{' '}
                      {f.slot.timestamp ?? f.slot.start_ts}s
                    </Text>
                    <Button size="compact-xs" variant="light" loading={busy}
                      disabled={f.kind !== 'dialogue' && !(dirResult.intent.brand || brandSel[0])}
                      onClick={() => placeResolved(f)}>
                      Use this
                    </Button>
                  </Group>
                ))}
              </Stack>
            )}

            {!(dirResult.intent.variants?.length || brandSel[0]) && !dirResult.needs_clarification && (
              <Text size="xs" c="yellow">
                No brand matched the catalog — name one, or pick it in Manual controls.
              </Text>
            )}
          </Stack>
        )}
      </Paper>

      <Collapse expanded={showManual}>
      <Group justify="flex-end" mb="sm">
        <SegmentedControl
          size="xs" value={mode} onChange={setMode}
          data={[
            { value: 'visual', label: 'Visual placement' },
            { value: 'dialogue', label: 'Dialogue swap (seamless)' },
            { value: 'gap', label: 'Gap spot' },
          ]}
        />
      </Group>

      <Group align="flex-end" gap="sm" mb="sm">
        <Select
          label="Target audience" size="xs" w={260} clearable
          placeholder="All viewers (no targeting)"
          data={audiences.map((a) => ({ value: a.id, label: a.label }))}
          value={audienceId} onChange={setAudienceId}
        />
        <Text size="xs" c="dimmed" pb={6}>
          {audienceId
            ? 'Brands ranked by demographic fit; swap + script generation targets this segment.'
            : 'Pick a segment to bias brand choice toward that demographic.'}
        </Text>
      </Group>

      {mode === 'visual' && (
        (analysis.visual_slots || []).length === 0
          ? <Text size="xs" c="dimmed">No visual placements detected for this video.</Text>
          : <Group align="flex-end" gap="sm" wrap="wrap">
              <Select
                label="Detected surface" size="xs" w={330}
                data={analysis.visual_slots.map((s, i) => ({
                  value: String(i),
                  label: `t=${s.timestamp}s — ${s.surface} (${s.score}/10)`,
                }))}
                value={visualIdx} onChange={setVisualIdx}
              />
              <MultiSelect
                label="Brand(s)" size="xs" w={240} searchable
                data={brandOptions}
                value={brandSel} onChange={setBrandSel}
              />
              <Select
                label="Quality" size="xs" w={150}
                data={[
                  { value: 'draft', label: 'Draft (fast, cheap)' },
                  { value: 'final', label: 'Final (Aleph)' },
                ]}
                value={vQuality} onChange={setVQuality}
              />
              <Button size="xs" onClick={generate} loading={busy} disabled={visualIdx === null || brandSel.length === 0}>
                {chain ? '+ Add visual ad' : 'Place visual ad'}
              </Button>
            </Group>
      )}

      {mode === 'dialogue' && (
        <>
          <Group align="flex-end" gap="sm" wrap="wrap" mb={liveSwaps.length ? 'xs' : 0}>
            <MultiSelect
              label="Target brand(s) (optional)" size="xs" w={220} clearable searchable
              data={brandOptions}
              value={swapBrand || []} onChange={setSwapBrand}
            />
            <Button size="xs" variant="light" loading={rescanning} onClick={async () => {
              setRescanning(true)
              const res = await fetch('/api/rescan_swaps', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: video.filename, brand: swapBrand, audience: audienceId }),
              }).then((r) => r.json())
              setRescanning(false)
              if (!res.error) { setLiveSwaps(res.dialogue_swaps); setSwapIdx(null) }
            }}>
              Scan for swaps
            </Button>
          </Group>
          {liveSwaps.length === 0
            ? <Text size="xs" c="dimmed">No swap proposals — pick a target brand and scan, or leave blank for all brands.</Text>
            : <Group align="flex-end" gap="sm" wrap="wrap">
                <Select
                  label="Proposed swap" size="xs" w={420}
                  data={liveSwaps.map((s, i) => ({
                    value: String(i),
                    label: `[${s.brand}] "${s.full_line_after}" @ ${s.start_ts}s — lip-sync: ${s.lip_sync?.risk ?? '?'}`,
                  }))}
                  value={swapIdx} onChange={setSwapIdx}
                />
                <Button size="xs" onClick={generate} loading={busy} disabled={swapIdx === null}>
                  {chain ? '+ Add re-voiced line' : 'Re-voice line'}
                </Button>
              </Group>}
        </>
      )}

      {mode === 'gap' && (
        <>
          <Group align="flex-end" gap="sm" wrap="wrap">
            <MultiSelect
              label="Brand(s)" size="xs" w={240} searchable
              data={brands.map((b) => ({
                value: b.name,
                label: b.audience_score !== undefined
                  ? `${b.name} (${b.category}) — fit ${Math.round(b.audience_score * 100)}%`
                  : `${b.name} (${b.category})`,
              }))}
              value={brandSel} onChange={setBrandSel}
            />
            <Select
              label="Ad gap" size="xs" w={200}
              data={gaps} value={gap} onChange={setGap}
            />
            <Button size="xs" onClick={generate} loading={busy} disabled={brandSel.length === 0 || gap === null}>
              {chain ? '+ Add audio ad' : 'Generate audio ad'}
            </Button>
          </Group>
          <Textarea
            label="Creative direction (optional — defaults to detected scene context)"
            placeholder={sceneDefault || 'e.g. 1980s supermarket PA announcement, warm tone'}
            size="xs" mt="sm" autosize minRows={1}
            value={context} onChange={(e) => setContext(e.currentTarget.value)}
          />
        </>
      )}
      </Collapse>
      {error && <Text size="xs" c="red" mt="xs">{error}</Text>}

      {latest && (
        <Box mt="md">
          <Group gap={8} mb={6} justify="space-between">
            <Group gap={8}>
              <Badge size="sm" color="teal" variant="light">
                {results.length} edit{results.length > 1 ? 's' : ''} stacked
              </Badge>
              <Text size="xs" c="dimmed">next edit adds on top of this result</Text>
            </Group>
            <Button size="compact-xs" variant="subtle" color="red" onClick={() => { setResults([]); setSessionId(null) }}>
              Reset stack
            </Button>
          </Group>
          <Stack gap={4} mb="sm">
            {results.map((r, i) => (
              <Group key={r.key} gap={8}>
                <Badge size="xs" variant="outline" color="gray">#{i + 1}</Badge>
                <Badge size="xs" variant="light" color={r.surface ? 'teal' : r.script ? 'indigo' : 'pink'}>
                  {r.surface ? 'visual ad' : r.script ? 'gap spot' : 'dialogue swap'}
                </Badge>
                <Text size="xs" c="dimmed" lineClamp={1} style={{ flex: 1 }}>
                  {r.surface
                    ? `${r.brand} on ${r.surface}${r.windows ? ` (${r.windows.map(w => w.join('–')).join(', ')}s)` : r.seg_start !== undefined ? ` @ ${r.seg_start}–${r.seg_end}s` : ''}`
                    : r.script
                      ? `"${r.script}" @ ${r.start_ts}s`
                      : `"${r.line_after}" @ ${r.at_ts}s (${r.engine})`}
                </Text>
              </Group>
            ))}
          </Stack>
          {(() => {
            // one player per distinct output video: variants (own sessions)
            // render side by side; chained edits collapse to the newest
            const seen = new Map()
            for (const r of results) {
              const k = r.session_id || 'main'
              seen.set(k, r)  // later results win within a session
            }
            const players = [...seen.values()]
            return (
              <Group align="flex-start" gap="md" wrap="wrap">
                {players.map((r) => (
                  <Box key={r.key} w={players.length > 1 ? '45%' : '60%'}>
                    {players.length > 1 && (
                      <Badge size="xs" variant="light" mb={4}>{r.brand || r.engine}</Badge>
                    )}
                    <video
                      src={`${players.length > 1 && r.session_id
                        ? `/static/uploads/sessions/${r.session_id}.mp4`
                        : r.output || (resume && resume.video)}?v=${r.key}`}
                      controls preload="metadata"
                      style={{ width: '100%', borderRadius: 8, background: '#000' }}
                    />
                  </Box>
                ))}
              </Group>
            )
          })()}
        </Box>
      )}
    </Paper>
  )
}

const KIND_META = {
  visual: { color: 'orange', label: 'visual' },
  dialogue_swap: { color: 'pink', label: 'dialogue swap' },
  gap_spot: { color: 'indigo', label: 'gap spot' },
}

function SessionsDashboard({ onContinue }) {
  const [sessions, setSessions] = useState(null)

  const load = () => api('/api/sessions').then(setSessions)
  useEffect(() => { load() }, [])

  const remove = async (id) => {
    await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
    load()
  }

  if (!sessions) return <Loader size="sm" mt="xl" mx="auto" />
  if (sessions.length === 0) {
    return <Text c="dimmed" ta="center" mt="30vh">No sessions yet — each stack of ad edits you build is archived here.</Text>
  }

  return (
    <Stack gap="md">
      <Title order={5}>Ad integration sessions ({sessions.length})</Title>
      {sessions.map((s) => (
        <Paper key={s.id} p="md" radius="md" withBorder>
          <Group justify="space-between" mb={6}>
            <Group gap={8}>
              <Badge size="sm" color="teal" variant="light">
                {s.edits.length} edit{s.edits.length > 1 ? 's' : ''}
              </Badge>
              <Text size="xs" ff="monospace" c="dimmed" lineClamp={1} maw={280}>{s.filename}</Text>
              <Text size="xs" c="dimmed">{s.updated_at || s.created_at}</Text>
            </Group>
            <Group gap={4}>
              <Button size="compact-xs" variant="light" onClick={() => onContinue(s)}>
                Continue
              </Button>
              <Button size="compact-xs" variant="subtle" color="red" onClick={() => remove(s.id)}>
                Delete
              </Button>
            </Group>
          </Group>
          <Stack gap={4} mb={8}>
            {s.edits.map((e, i) => {
              const meta = KIND_META[e.kind] || { color: 'gray', label: e.kind }
              const d = e.detail || {}
              return (
                <Group key={i} gap={8}>
                  <Badge size="xs" variant="outline" color="gray">#{i + 1}</Badge>
                  <Badge size="xs" color={meta.color} variant="light">{meta.label}</Badge>
                  {d.brand && <Badge size="xs" variant="outline">{d.brand}</Badge>}
                  <Text size="xs" c="dimmed" lineClamp={1} style={{ flex: 1 }}>
                    {d.script || d.line_after || d.prompt || ''}
                  </Text>
                </Group>
              )
            })}
          </Stack>
          <video src={`${s.video}?v=${s.updated_at}`} controls preload="metadata"
                 style={{ width: '55%', borderRadius: 8, background: '#000' }} />
        </Paper>
      ))}
    </Stack>
  )
}

const scoreColor = (s) => (s >= 8 ? 'teal' : s >= 6 ? 'yellow' : 'gray')

function Timeline({ analysis, onSeek }) {
  const dur = analysis.duration
  const visual = analysis.visual_slots.filter((s) => s.score >= 5)
  const pct = (t) => `${(t / dur) * 100}%`

  // group visual slots by timestamp (one frame -> possibly many surfaces)
  const byFrame = {}
  visual.forEach((s) => { (byFrame[s.timestamp] ??= []).push(s) })

  const ticks = []
  const step = dur > 120 ? 30 : dur > 40 ? 10 : 5
  for (let t = 0; t <= dur; t += step) ticks.push(t)

  return (
    <Paper p="md" radius="md" withBorder>
      <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb="sm">Timeline</Text>

      {/* ruler */}
      <Box pos="relative" h={18} ml={90}>
        {ticks.map((t) => (
          <Text key={t} size="10px" c="dimmed" pos="absolute" left={pct(t)} style={{ transform: 'translateX(-50%)' }}>
            {t}s
          </Text>
        ))}
      </Box>

      {/* visual track */}
      <TrackRow icon={<IconPhoto size={13} />} label="Visual">
        {Object.entries(byFrame).map(([ts, slots]) => {
          const best = slots.reduce((a, b) => (b.score > a.score ? b : a))
          return (
            <HoverCard key={ts} width={320} shadow="lg" openDelay={100} position="top">
              <HoverCard.Target>
                <Box
                  onClick={() => onSeek(+ts)}
                  pos="absolute"
                  left={pct(+ts)}
                  top={4}
                  bottom={4}
                  w={rem(14)}
                  bg={`var(--mantine-color-${scoreColor(best.score)}-6)`}
                  style={{ borderRadius: 3, cursor: 'pointer', transform: 'translateX(-50%)' }}
                />
              </HoverCard.Target>
              <HoverCard.Dropdown p="xs">
                <FramePreview slots={slots} ts={ts} />
              </HoverCard.Dropdown>
            </HoverCard>
          )
        })}
      </TrackRow>

      {/* integrations track */}
      {(analysis.integrations || []).length > 0 && (
        <TrackRow icon={<IconSparkles size={13} />} label="Integrate">
          {analysis.integrations.map((s, i) => (
            <HoverCard key={i} width={300} shadow="lg" openDelay={100} position="top">
              <HoverCard.Target>
                <Box
                  onClick={() => onSeek(s.start_ts)}
                  pos="absolute"
                  left={pct(s.start_ts)}
                  w={`calc(${((s.end_ts - s.start_ts) / dur) * 100}%)`}
                  top={4}
                  bottom={4}
                  bg={s.kind === 'product_interaction' ? 'var(--mantine-color-pink-6)' : 'var(--mantine-color-grape-6)'}
                  opacity={0.85}
                  style={{ borderRadius: 3, cursor: 'pointer', minWidth: 8 }}
                />
              </HoverCard.Target>
              <HoverCard.Dropdown p="xs">
                <Stack gap={6}>
                  <Group gap={6}>
                    <Badge size="xs" color={s.kind === 'product_interaction' ? 'pink' : 'grape'} variant="light">{s.kind}</Badge>
                    <Badge size="xs" variant="light">{s.start_ts}–{s.end_ts}s</Badge>
                    <Badge size="xs" color={scoreColor(s.score)} variant="light">{s.score}/10</Badge>
                  </Group>
                  <Text size="xs">{s.description}</Text>
                  {s.example_categories && (
                    <Group gap={4}>
                      {s.example_categories.map((c, j) => <Badge key={j} size="xs" color="gray" variant="outline">{c}</Badge>)}
                    </Group>
                  )}
                </Stack>
              </HoverCard.Dropdown>
            </HoverCard>
          ))}
        </TrackRow>
      )}

      {/* speech track */}
      {(analysis.transcript || []).length > 0 && (
        <TrackRow icon={<IconMessage size={13} />} label="Speech">
          {analysis.transcript.map((s, i) => (
            <Tooltip key={i} label={`"${s.text}" (${s.start_ts}–${s.end_ts}s)`} multiline w={280}>
              <Box
                onClick={() => onSeek(s.start_ts)}
                pos="absolute"
                left={pct(s.start_ts)}
                w={`calc(${((s.end_ts - s.start_ts) / dur) * 100}%)`}
                top={12}
                bottom={12}
                bg="var(--mantine-color-cyan-7)"
                opacity={0.8}
                style={{ borderRadius: 2, cursor: 'pointer', minWidth: 3 }}
              />
            </Tooltip>
          ))}
        </TrackRow>
      )}

      {/* audio track */}
      <TrackRow icon={<IconWaveSine size={13} />} label="Ad gaps">
        {analysis.audio_slots.map((s, i) => (
          <Tooltip key={i} label={`${s.kind === 'dialogue_gap' ? 'dialogue gap' : 'silence'}: ${s.start_ts}s → ${s.end_ts}s (${s.duration}s)`}>
            <Box
              onClick={() => onSeek(s.start_ts)}
              pos="absolute"
              left={pct(s.start_ts)}
              w={`calc(${((s.end_ts - s.start_ts) / dur) * 100}% )`}
              top={6}
              bottom={6}
              bg="var(--mantine-color-indigo-6)"
              opacity={0.75}
              style={{ borderRadius: 3, cursor: 'pointer', minWidth: 4 }}
            />
          </Tooltip>
        ))}
      </TrackRow>

      <Group gap="lg" mt="sm" ml={90}>
        <LegendDot color="teal" label="strong placement (8+)" />
        <LegendDot color="yellow" label="decent (6-7)" />
        <LegendDot color="gray" label="weak (5)" />
        <LegendDot color="indigo" label="audio gap" />
        <LegendDot color="pink" label="product interaction" />
        <LegendDot color="grape" label="other integration" />
      </Group>
    </Paper>
  )
}

function TrackRow({ icon, label, children }) {
  return (
    <Group gap={0} mb={8} wrap="nowrap" align="stretch">
      <Group gap={6} w={90} px={8} style={{ flexShrink: 0 }}>
        {icon}
        <Text size="xs" fw={600}>{label}</Text>
      </Group>
      <Box pos="relative" h={44} style={{ flex: 1, background: 'var(--mantine-color-dark-8)', borderRadius: 6, border: '1px solid var(--mantine-color-dark-5)' }}>
        {children}
      </Box>
    </Group>
  )
}

function FramePreview({ slots, ts }) {
  const frame = slots[0].frame
  return (
    <Stack gap={6}>
      <Box pos="relative">
        <Image src={frame} radius="sm" />
        {slots.map((s, i) => {
          const [x1, y1, x2, y2] = s.bbox
          return (
            <Box
              key={i}
              pos="absolute"
              style={{
                left: `${x1 / 10}%`, top: `${y1 / 10}%`,
                width: `${(x2 - x1) / 10}%`, height: `${(y2 - y1) / 10}%`,
                border: '1.5px solid var(--mantine-color-red-5)',
                background: 'rgba(250,82,82,.12)', borderRadius: 2,
              }}
            />
          )
        })}
      </Box>
      <Group gap={6}>
        <Badge size="xs" variant="light">t={ts}s</Badge>
        {slots.map((s, i) => (
          <Badge key={i} size="xs" color={scoreColor(s.score)} variant="light">
            {s.surface} {s.score}/10
          </Badge>
        ))}
      </Group>
      <Text size="xs" c="dimmed" lineClamp={2}>
        {slots.reduce((a, b) => (b.score > a.score ? b : a)).reason}
      </Text>
    </Stack>
  )
}

function Player({ src, videoRef, width = '60%', mt }) {
  const localRef = useRef(null)
  const ref = videoRef || localRef
  const [theater, setTheater] = useState(false)

  const expand = () => {
    const el = ref.current
    if (!el) return
    const fs = el.requestFullscreen || el.webkitRequestFullscreen || el.webkitEnterFullscreen
    if (fs) {
      try {
        const p = fs.call(el)
        if (p && p.then) {
          p.catch(() => setTheater(true))
          return
        }
        return
      } catch { /* blocked -> theater */ }
    }
    setTheater(true)
  }

  return (
    <>
      <Box pos="relative" w={width} mt={mt}>
        <video ref={ref} src={src} controls style={{ width: '100%', borderRadius: 8, background: '#000', display: 'block' }} />
        <Button
          size="compact-xs" variant="default"
          pos="absolute" top={8} right={8}
          onClick={expand}
          title="Expand"
        >
          <IconMaximize size={13} />
        </Button>
      </Box>
      {theater && (
        <Box
          pos="fixed" inset={0}
          style={{ zIndex: 1000, background: 'rgba(0,0,0,0.92)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          onClick={() => setTheater(false)}
        >
          <video src={src} controls autoPlay style={{ maxWidth: '96vw', maxHeight: '92vh', borderRadius: 8 }} onClick={(e) => e.stopPropagation()} />
          <Button pos="absolute" top={16} right={16} variant="default" size="compact-sm" onClick={() => setTheater(false)}>
            ✕ Close
          </Button>
        </Box>
      )}
    </>
  )
}

function LegendDot({ color, label }) {
  return (
    <Group gap={5}>
      <Box w={10} h={10} bg={`var(--mantine-color-${color}-6)`} style={{ borderRadius: 2 }} />
      <Text size="xs" c="dimmed">{label}</Text>
    </Group>
  )
}
