import { useEffect, useRef } from 'react'
import { createChart, ColorType, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts'

export interface Bar {
  open_time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/**
 * K 线图封装。
 *
 * `lightweight-charts` 要求数据严格升序且无重复时间戳——它不报错，只是画得不对。
 * 所以这里先排序去重再喂进去：API 端的降采样语义固定（open=首/close=末/volume=sum），
 * 但"已经去重"这件事不应该由图表组件假定。
 */
export default function BarChart({ bars, height = 360 }: { bars: Bar[]; height?: number }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#8b949e',
      },
      grid: {
        vertLines: { color: '#1c222b' },
        horzLines: { color: '#1c222b' },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#262d37' },
    })
    const series = chart.addCandlestickSeries({
      upColor: '#3fb950',
      downColor: '#f85149',
      borderVisible: false,
      wickUpColor: '#3fb950',
      wickDownColor: '#f85149',
    })
    chartRef.current = chart
    seriesRef.current = series

    const onResize = () => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth })
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height])

  useEffect(() => {
    if (!seriesRef.current) return
    const seen = new Set<number>()
    const data = bars
      .map((b) => ({
        time: Math.floor(new Date(b.open_time).getTime() / 1000) as UTCTimestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
      .filter((d) => (seen.has(d.time) ? false : (seen.add(d.time), true)))
      .sort((a, b) => (a.time as number) - (b.time as number))
    seriesRef.current.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [bars])

  return <div ref={containerRef} />
}
