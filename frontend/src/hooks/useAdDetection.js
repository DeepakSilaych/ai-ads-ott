import { useCallback, useEffect, useRef, useState } from 'react'

export const api = (path, opts) => fetch(path, opts).then((r) => r.json())

export function useVideos() {
  const [videos, setVideos] = useState([])
  const reload = useCallback(() => api('/api/videos').then(setVideos), [])
  useEffect(() => { reload() }, [reload])
  return { videos, reload }
}

export function useAdDetection(video) {
  const [analysis, setAnalysis] = useState(null)
  const [job, setJob] = useState(null)
  const pollRef = useRef(null)

  const fetchStatus = useCallback(async () => {
    const j = await api(`/api/detect/${video.video_id}`)
    if (j.status === 'done') {
      setAnalysis(j.result)
      setJob(null)
    } else if (j.status === 'running') {
      setJob(j)
    } else {
      setJob(null)
    }
    return j
  }, [video.video_id])

  useEffect(() => {
    fetchStatus()
    return () => clearInterval(pollRef.current)
  }, [fetchStatus])

  useEffect(() => {
    if (!job) return
    pollRef.current = setInterval(async () => {
      const j = await fetchStatus()
      if (j.status !== 'running') clearInterval(pollRef.current)
    }, 1500)
    return () => clearInterval(pollRef.current)
  }, [job !== null, fetchStatus])

  const detect = useCallback(async () => {
    setJob({ status: 'running', progress: 'starting' })
    await api('/api/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: video.filename }),
    })
  }, [video.filename])

  return { analysis, job, detect }
}
