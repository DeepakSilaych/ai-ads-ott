import { useEffect, useRef, useState } from 'react'
import {
  AppShell, Badge, Box, Button, Card, Group, HoverCard, Image, Loader,
  Paper, ScrollArea, SegmentedControl, Select, Stack, Text, Textarea, Title, Tooltip, rem,
} from '@mantine/core'
import {
  IconMaximize, IconMessage, IconMovie, IconPhoto, IconRefresh, IconScan, IconSparkles, IconVolume, IconWaveSine,
} from '@tabler/icons-react'
import { api, useAdDetection, useVideos } from './hooks/useAdDetection.js'

export default function App() {
  const { videos, reload: load } = useVideos()
  const [selected, setSelected] = useState(null)

  return (
    <AppShell navbar={{ width: 240, breakpoint: 0 }} padding="md">
      <AppShell.Navbar p="md">
        <Group justify="space-between" mb="md">
          <Group gap={8}>
            <IconMovie size={20} />
            <Title order={5}>Ad Insertion</Title>
          </Group>
          <Button variant="subtle" size="compact-xs" onClick={load}>
            <IconRefresh size={14} />
          </Button>
        </Group>
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
        {selected
          ? <VideoDetail video={selected} key={selected.video_id} />
          : <Text c="dimmed" ta="center" mt="30vh">Select a video</Text>}
      </AppShell.Main>
    </AppShell>
  )
}

function VideoDetail({ video }) {
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
      {analysis && <AudioBranding video={video} analysis={analysis} />}
      {!analysis && !job && <Text size="sm" c="dimmed">Run detection to see ad placement opportunities.</Text>}
    </Stack>
  )
}

function AudioBranding({ video, analysis }) {
  const [mode, setMode] = useState('visual')
  const [visualIdx, setVisualIdx] = useState(null)
  const [brands, setBrands] = useState([])
  const [brand, setBrand] = useState(null)
  const [gap, setGap] = useState(null)
  const [swapIdx, setSwapIdx] = useState(null)
  const [context, setContext] = useState('')
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState([])
  const [error, setError] = useState(null)
  // edits stack automatically: first edit starts from the original,
  // every subsequent edit chains on top of the previous result
  const chain = results.length > 0

  useEffect(() => { api('/api/brands').then(setBrands) }, [])

  const swaps = analysis.dialogue_swaps || []

  const gaps = analysis.audio_slots.map((s, i) => ({
    value: String(i),
    label: `${s.start_ts}s → ${s.end_ts}s  (${s.duration}s)`,
  }))

  const sceneDefault = (analysis.integrations || []).map((x) => x.description).join('; ')

  const generate = async () => {
    setBusy(true); setError(null)
    let res
    if (mode === 'visual') {
      res = await fetch('/api/place_visual', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          slot_index: +visualIdx,
          brand,
          chain,
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
        }),
      }).then((r) => r.json())
    } else {
      const slot = analysis.audio_slots[+gap]
      res = await fetch('/api/place_audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: video.filename,
          brand,
          start_ts: slot.start_ts,
          gap_duration: slot.duration,
          scene_context: context || sceneDefault,
          chain,
        }),
      }).then((r) => r.json())
    }
    setBusy(false)
    if (res.error) { setError(res.error); return }
    setResults((prev) => [...prev, { ...res, key: Date.now() }])
  }

  const latest = results[results.length - 1]

  return (
    <Paper p="md" radius="md" withBorder>
      <Group justify="space-between" mb="sm">
        <Text size="xs" c="dimmed" tt="uppercase" fw={700}>Audio Branding</Text>
        <SegmentedControl
          size="xs" value={mode} onChange={setMode}
          data={[
            { value: 'visual', label: 'Visual placement' },
            { value: 'dialogue', label: 'Dialogue swap (seamless)' },
            { value: 'gap', label: 'Gap spot' },
          ]}
        />
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
              <Select
                label="Brand" size="xs" w={160} searchable
                data={brands.map((b) => ({ value: b.name, label: b.name }))}
                value={brand} onChange={setBrand}
              />
              <Button size="xs" onClick={generate} loading={busy} disabled={visualIdx === null || !brand}>
                {chain ? '+ Add visual ad' : 'Place visual ad'}
              </Button>
            </Group>
      )}

      {mode === 'dialogue' && (
        swaps.length === 0
          ? <Text size="xs" c="dimmed">No dialogue swap opportunities detected for this video.</Text>
          : <Group align="flex-end" gap="sm" wrap="wrap">
              <Select
                label="Detected swap" size="xs" w={420}
                data={swaps.map((s, i) => ({
                  value: String(i),
                  label: `[${s.brand}] "${s.full_line_after}" @ ${s.start_ts}s — lip-sync: ${s.lip_sync?.risk ?? '?'}`,
                }))}
                value={swapIdx} onChange={setSwapIdx}
              />
              <Button size="xs" onClick={generate} loading={busy} disabled={swapIdx === null}>
                {chain ? '+ Add re-voiced line' : 'Re-voice line'}
              </Button>
            </Group>
      )}

      {mode === 'gap' && (
        <>
          <Group align="flex-end" gap="sm" wrap="wrap">
            <Select
              label="Brand" size="xs" w={160} searchable
              data={brands.map((b) => ({ value: b.name, label: `${b.name} (${b.category})` }))}
              value={brand} onChange={setBrand}
            />
            <Select
              label="Ad gap" size="xs" w={200}
              data={gaps} value={gap} onChange={setGap}
            />
            <Button size="xs" onClick={generate} loading={busy} disabled={!brand || gap === null}>
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
            <Button size="compact-xs" variant="subtle" color="red" onClick={() => setResults([])}>
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
                    ? `${r.brand} on ${r.surface} @ ${r.start_ts}–${r.end_ts}s`
                    : r.script
                      ? `"${r.script}" @ ${r.start_ts}s`
                      : `"${r.line_after}" @ ${r.at_ts}s (${r.engine})`}
                </Text>
              </Group>
            ))}
          </Stack>
          <Player key={latest.key} src={`${latest.output}?v=${latest.key}`} />
        </Box>
      )}
    </Paper>
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
