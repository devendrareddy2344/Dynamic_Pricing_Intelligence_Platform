import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  Cell,
} from "recharts";
import {
  Search,
  ShoppingCart,
  TrendingUp,
  AlertCircle,
  CheckCircle2,
  Globe,
  Camera,
  History as HistoryIcon,
  Zap,
  ArrowRight,
  ExternalLink,
} from "lucide-react";

type Vision = {
  session_id: string;
  product_name: string;
  brand: string;
  category: string;
  key_specs: string[];
  search_queries: string[];
  confidence: number;
  notes: string;
  low_confidence_warning: boolean;
  product_hash: string;
  cache_hit: boolean;
};

type PriceEvt = {
  event: string;
  source?: string;
  price?: number;
  currency?: string;
  product_name?: string;
  product_url?: string;
  seller_rating?: number;
  review_count?: number;
  in_stock?: boolean;
  title_match_score?: number;
  reason?: string;
  recommended_price?: number;
  price_range?: { low: number; high: number };
  strategy?: string;
  competitive_score?: number;
  ml?: Record<string, unknown>;
  token?: string;
};

const API = import.meta.env.VITE_API_BASE ?? "";

const STORE_CONFIG: Record<string, { color: string; bg: string; border: string }> = {
  amazon: { color: "#ff9900", bg: "bg-[#ff9900]/10", border: "border-[#ff9900]/30" },
  flipkart: { color: "#2874f0", bg: "bg-[#2874f0]/10", border: "border-[#2874f0]/30" },
  walmart: { color: "#ffc220", bg: "bg-[#ffc220]/10", border: "border-[#ffc220]/30" },
  bestbuy: { color: "#fff200", bg: "bg-[#fff200]/10", border: "border-[#fff200]/30" },
  croma: { color: "#00e9bf", bg: "bg-[#00e9bf]/10", border: "border-[#00e9bf]/30" },
};

const ALL_SITES = Object.keys(STORE_CONFIG);

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [loadingVision, setLoadingVision] = useState(false);
  const [vision, setVision] = useState<Vision | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [scraping, setScraping] = useState(false);
  const [siteState, setSiteState] = useState<Record<string, "queued" | "scraping" | "found" | "failed">>(() =>
    Object.fromEntries(ALL_SITES.map((s) => [s, "queued"])) as Record<string, "queued">
  );

  const [prices, setPrices] = useState<PriceEvt[]>([]);
  const [analysis, setAnalysis] = useState<PriceEvt | null>(null);
  const [genaiText, setGenaiText] = useState("");

  const chartData = useMemo(
    () =>
      prices
        .filter((p) => p.event === "price_scraped")
        .map((p) => ({ 
          name: p.source ?? "?", 
          price: p.price ?? 0,
          color: STORE_CONFIG[p.source ?? ""]?.color ?? "#94a3b8"
        })),
    [prices]
  );

  const stats = useMemo(() => {
    const vals = chartData.map((d) => d.price).filter((n) => n > 0);
    if (!vals.length) return { min: 0, max: 0, avg: 0, n: 0 };
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    return { min, max, avg, n: vals.length };
  }, [chartData]);

  const onFile = useCallback((f: File | null) => {
    setFile(f);
    setVision(null);
    setPrices([]);
    setAnalysis(null);
    setGenaiText("");
    setError(null);
    setSiteState(Object.fromEntries(ALL_SITES.map((s) => [s, "queued"])) as Record<string, "queued">);
    if (f) {
      const url = URL.createObjectURL(f);
      setPreview(url);
    } else setPreview(null);
  }, []);

  const runVision = async () => {
    console.log("Run Vision triggered with file:", file);
    if (!file) {
      console.warn("No file selected.");
      return;
    }
    setLoadingVision(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API}/api/v1/vision`, { method: "POST", body: fd });
      if (!r.ok) throw new Error(await r.text());
      const data = (await r.json()) as Vision;
      setVision(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoadingVision(false);
    }
  };

  const runPipeline = async () => {
    if (!vision) return;
    setScraping(true);
    setPrices([]);
    setAnalysis(null);
    setGenaiText("");
    setSiteState(Object.fromEntries(ALL_SITES.map((s) => [s, "scraping"])) as Record<string, "scraping">);

    const es = new EventSource(`${API}/api/v1/sessions/${vision.session_id}/stream`);

    const onPrice = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as PriceEvt;
        if (msg.event === "price_scraped" && msg.source) {
          setPrices((p) => [...p, msg]);
          setSiteState((s) => ({ ...s, [msg.source!]: "found" }));
        }
      } catch {
        /* ignore */
      }
    };
    const onFail = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as PriceEvt;
        if (msg.source) {
          const src = msg.source;
          setSiteState((s) => ({ ...s, [src as string]: "failed" }));
        }
      } catch {
        /* ignore */
      }
    };

    const onAnalysis = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as PriceEvt;
        setAnalysis(msg);
      } catch {
        /* ignore */
      }
    };
    const onToken = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data) as PriceEvt;
        if (msg.token) setGenaiText((t) => t + msg.token);
      } catch {
        /* ignore */
      }
    };
    const onDone = () => {
      es.close();
      setScraping(false);
    };

    es.addEventListener("price_scraped", onPrice);
    es.addEventListener("scraper_failed", onFail);
    es.addEventListener("analysis_ready", onAnalysis);
    es.addEventListener("genai_token", onToken);
    es.addEventListener("genai_done", onDone);
    es.addEventListener("done", onDone);

    const r = await fetch(`${API}/api/v1/sessions/${vision.session_id}/scrape`, { method: "POST" });
    if (!r.ok) {
      setError(await r.text());
      es.close();
      setScraping(false);
    }
  };

  return (
    <div className="min-h-screen">
      {/* Premium Gradient Header */}
      <header className="relative border-b border-white/5 bg-ink-950 px-6 py-10 shadow-2xl overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(0,242,254,0.1),transparent)]" />
        <div className="absolute -left-20 top-0 h-40 w-40 bg-accent/20 blur-[100px] rounded-full" />
        
        <div className="relative mx-auto flex max-w-6xl flex-col items-start gap-4 md:flex-row md:items-center md:justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <div className="rounded-lg bg-accent px-2 py-1 text-[10px] font-black uppercase tracking-tighter text-ink-950">V2.0</div>
              <p className="text-xs font-bold uppercase tracking-[0.3em] text-accent/80">Synycs Intelligence</p>
            </div>
            <h1 className="mt-1 text-3xl font-black text-white md:text-5xl tracking-tight text-glow">
              Automated Pricing Engine
            </h1>
            <p className="mt-4 max-w-2xl text-sm leading-relaxed text-slate-400">
              Transform product visuals into competitive market data. Vision AI identification, 
              real-time multi-platform extraction, and GenAI pricing rationale.
            </p>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-4 py-2 text-xs font-semibold text-slate-300">
              <Zap className="h-3.5 w-3.5 text-accent animate-pulse" />
              Real-time Active
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-12 px-6 py-12">
        {/* Section 1: Interaction Hub */}
        <div className="grid gap-8 lg:grid-cols-12">
          {/* File Upload Area */}
          <div className="lg:col-span-12">
            <div className="glass rounded-3xl p-8 relative overflow-hidden group">
              <div className="absolute inset-0 bg-accent/5 opacity-0 group-hover:opacity-100 transition-opacity" />
              
              <div className="flex items-center justify-between mb-6">
                <div>
                  <h2 className="flex items-center gap-2 text-lg font-bold text-white">
                    <Camera className="h-5 w-5 text-accent" />
                    Visual Recognition Hub
                  </h2>
                  <p className="text-xs text-slate-500 mt-1 uppercase tracking-widest font-semibold">Drop image to begin</p>
                </div>
              </div>

              <div
                className="relative cursor-pointer rounded-2xl border-2 border-dashed border-white/10 bg-black/40 px-6 py-12 text-center transition-all hover:border-accent/40 active:scale-[0.99]"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  const f = e.dataTransfer.files?.[0];
                  if (f) onFile(f);
                }}
                onClick={() => document.getElementById("file-input")?.click()}
              >
                <input id="file-input" type="file" accept="image/*" className="hidden" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
                {preview ? (
                  <div className="relative inline-block group">
                    <img src={preview} alt="preview" className="max-h-72 rounded-xl object-contain shadow-2xl ring-1 ring-white/20" />
                    {loadingVision && <div className="scanner-line" />}
                    <div className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity rounded-xl">
                      <p className="text-xs font-bold text-white">Change Image</p>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-center">
                    <div className="h-16 w-16 rounded-3xl bg-accent/10 flex items-center justify-center mb-4 ring-1 ring-white/10">
                      <Search className="h-8 w-8 text-accent" />
                    </div>
                    <p className="text-lg font-medium text-slate-300">Drag & drop or browse files</p>
                    <p className="mt-2 text-xs text-slate-500 uppercase tracking-widest">Max file size 10MB</p>
                  </div>
                )}
              </div>

              <div className="mt-8 flex items-center justify-center">
                <button
                  type="button"
                  key="analyze-btn"
                  disabled={!file || loadingVision}
                  onClick={() => {
                    console.log("Button Clicked!");
                    runVision();
                  }}
                  className="accent-gradient relative z-50 cursor-pointer pointer-events-auto rounded-2xl px-8 py-4 text-sm font-black text-ink-950 shadow-xl shadow-accent/20 transition hover:scale-[1.02] active:scale-[0.98] disabled:opacity-30 disabled:cursor-not-allowed flex items-center gap-3 uppercase tracking-widest"
                >
                  {loadingVision ? (
                    <><Zap className="h-4 w-4 animate-spin" /> Identifying Product...</>
                  ) : (
                    <><Search className="h-4 w-4" /> Analyse with Vision AI</>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-3 rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm text-rose-300 backdrop-blur-md">
            <AlertCircle className="h-5 w-5 shrink-0" />
            <p className="font-medium">{error}</p>
          </div>
        )}

        {/* Section 2: Identity & Recognition Results */}
        {vision && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="glass rounded-3xl p-8 relative">
              <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
                <div>
                  <div className="flex items-center gap-3 mb-2">
                    <span className="text-[10px] font-black uppercase tracking-[0.2em] text-accent">Identification complete</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${vision.confidence >= 0.8 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'}`}>
                      {(vision.confidence * 100).toFixed(0)}% Confidence
                    </span>
                  </div>
                  <h2 className="text-4xl font-black text-white tracking-tight">{vision.product_name}</h2>
                  <p className="text-slate-400 font-medium flex items-center gap-2 mt-2">
                    {vision.brand} <span className="h-1 w-1 rounded-full bg-slate-700" /> {vision.category}
                  </p>
                  <div className="mt-6 flex flex-wrap gap-2">
                    {vision.key_specs.map((s) => (
                      <span key={s} className="rounded-xl border border-white/5 bg-white/5 px-4 py-2 text-xs font-semibold text-slate-300">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex flex-col items-end gap-4 min-w-[240px]">
                  <button
                    type="button"
                    disabled={scraping}
                    onClick={runPipeline}
                    className="w-full rounded-2xl bg-white px-8 py-4 text-sm font-black text-ink-950 shadow-xl transition hover:bg-white/90 active:scale-[0.98] flex items-center justify-center gap-3 uppercase tracking-widest"
                  >
                    {scraping ? <Zap className="h-4 w-4 animate-spin" /> : <TrendingUp className="h-4 w-4" />}
                    {scraping ? "Extracting Data..." : "Run Intelligence"}
                  </button>
                  {vision.cache_hit && (
                    <div className="flex items-center gap-2 text-[10px] font-bold uppercase text-slate-500 tracking-widest">
                      <HistoryIcon className="h-3 w-3" /> Served from vault
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Section 3: Live Store Grid - "Show the Front End" */}
        {vision && (
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-lg font-bold text-white flex items-center gap-2 uppercase tracking-tight">
                  <Globe className="h-5 w-5 text-accent" /> Live Market Dashboard
                </h3>
                <p className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Streaming prices from 7 global platforms</p>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-7 xl:grid-cols-8">
              {ALL_SITES.map((s) => (
                <div
                  key={s}
                  className={`glass relative group rounded-2xl p-4 transition-all duration-300 ${
                    siteState[s] === 'scraping' ? 'ring-2 ring-accent animate-pulse shadow-[0_0_20px_rgba(0,242,254,0.2)]' : 
                    siteState[s] === 'found' ? 'ring-1 ring-emerald-500/30' : 'ring-1 ring-white/5'
                  }`}
                >
                  <div className={`absolute top-2 right-2 h-2 w-2 rounded-full ${
                    siteState[s] === 'found' ? 'bg-emerald-500 shadow-[0_0_10px_#10b981]' : 
                    siteState[s] === 'scraping' ? 'bg-accent shadow-[0_0_10px_#00f2fe]' : 
                    siteState[s] === 'failed' ? 'bg-rose-500 shadow-[0_0_10px_#f43f5e]' : 'bg-slate-700'
                  }`} />
                  
                  <div className={`h-12 w-12 rounded-xl mb-4 flex items-center justify-center transition-transform group-hover:scale-110 ${STORE_CONFIG[s].bg} ${STORE_CONFIG[s].border} border`}>
                    <span className="text-xl font-black uppercase text-white tracking-widest drop-shadow-lg">{s[0]}</span>
                  </div>
                  
                  <p className="text-xs font-black uppercase tracking-[0.15em] text-slate-200">{s}</p>
                  <p className={`mt-2 text-[10px] font-black uppercase tracking-widest ${
                    siteState[s] === 'found' ? 'text-emerald-400' : 'text-slate-500'
                  }`}>
                    {siteState[s] ?? "IDLE"}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}


        {/* Section 5: Data Analytics */}
        {chartData.length > 0 && (
          <div className="grid gap-8 lg:grid-cols-12">
            <div className="lg:col-span-8">
              <div className="glass rounded-3xl p-8 h-full">
                <div className="flex items-center justify-between mb-8">
                  <h3 className="font-bold text-white flex items-center gap-2 uppercase tracking-tight text-sm">
                    <TrendingUp className="h-4 w-4 text-accent" /> Platform Price Analysis
                  </h3>
                </div>
                <div className="h-[300px] w-full">
                  <ResponsiveContainer>
                    <BarChart data={chartData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="10 10" vertical={false} stroke="#ffffff08" />
                      <XAxis 
                        dataKey="name" 
                        stroke="#ffffff20" 
                        fontSize={10} 
                        tick={{ fill: "#94a3b8" }} 
                        axisLine={false} 
                        tickLine={false}
                        dy={10}
                      />
                      <YAxis 
                        stroke="#ffffff20" 
                        fontSize={10} 
                        tick={{ fill: "#94a3b8" }} 
                        axisLine={false} 
                        tickLine={false}
                        tickFormatter={(v) => `${v}`}
                      />
                      <Tooltip
                        cursor={{ fill: '#ffffff05' }}
                        contentStyle={{ background: "#0a0c10cc", border: "1px solid #ffffff10", borderRadius: '16px', backdropFilter: 'blur(10px)' }}
                        itemStyle={{ fontWeight: '800', fontSize: '12px' }}
                        labelStyle={{ fontWeight: '800', fontSize: '10px', color: '#64748b', textTransform: 'uppercase', marginBottom: '4px' }}
                      />
                      <Bar dataKey="price" radius={[8, 8, 0, 0]} barSize={40}>
                        {chartData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} fillOpacity={0.8} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            <div className="lg:col-span-4">
              <div className="grid grid-cols-1 gap-4 h-full">
                <div className="glass rounded-3xl p-6 bg-gradient-to-br from-white/5 to-transparent">
                  <p className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Insights Core</p>
                  <div className="space-y-6">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="h-8 w-8 rounded-xl bg-accent/10 flex items-center justify-center border border-accent/20">
                          <TrendingUp className="h-4 w-4 text-accent" />
                        </div>
                        <div>
                          <p className="text-[10px] font-bold text-slate-500 uppercase">Average</p>
                          <p className="text-xl font-black text-white">₹{stats.avg.toLocaleString()}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="h-8 w-8 rounded-xl bg-emerald-500/10 flex items-center justify-center border border-emerald-500/20">
                          <Zap className="h-4 w-4 text-emerald-400" />
                        </div>
                        <div>
                          <p className="text-[10px] font-bold text-slate-500 uppercase">Lowest</p>
                          <p className="text-xl font-black text-white">₹{stats.min.toLocaleString()}</p>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="h-8 w-8 rounded-xl bg-rose-500/10 flex items-center justify-center border border-rose-500/20">
                          <AlertCircle className="h-4 w-4 text-rose-400" />
                        </div>
                        <div>
                          <p className="text-[10px] font-bold text-slate-500 uppercase">Variance</p>
                          <p className="text-xl font-black text-white">₹{(stats.max - stats.min).toLocaleString()}</p>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                
                {analysis && (
                  <div className="accent-gradient rounded-3xl p-6 shadow-2xl shadow-accent/20">
                    <p className="text-[10px] font-black uppercase tracking-[0.2em] text-ink-950 mb-2">ML Recommended Target</p>
                    <p className="text-4xl font-black text-ink-950 tracking-tighter">₹{analysis.recommended_price?.toLocaleString()}</p>
                    <div className="mt-4 flex items-center gap-2">
                      <span className="rounded-full bg-ink-950/20 px-2 py-1 text-[10px] font-black uppercase tracking-tighter text-ink-950 border border-ink-950/10">
                        {analysis.strategy}
                      </span>
                      <span className="text-[10px] font-black uppercase tracking-tighter text-ink-950 opacity-60">
                        Score: {analysis.competitive_score}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Section 5: Dynamic Result Cards */}
        {prices.filter((p) => p.event === "price_scraped").length > 0 && (
          <div className="space-y-6">
            <h3 className="text-lg font-bold text-white uppercase tracking-tight flex items-center gap-2">
              <ShoppingCart className="h-5 w-5 text-accent" /> Verified Market Listings
            </h3>
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {prices
                .filter((p) => p.event === "price_scraped")
                .map((p, i) => (
                  <div key={`${p.source}-${i}`} className="glass group relative rounded-3xl p-6 transition-all hover:scale-[1.02] hover:bg-white/10 active:scale-[0.99] border-t-2 overflow-hidden" style={{ borderTopColor: STORE_CONFIG[p.source ?? '']?.color }}>
                    <div className="absolute top-0 right-0 p-4 opacity-10 group-hover:opacity-100 transition-opacity">
                      <ExternalLink className="h-4 w-4 text-white" />
                    </div>
                    
                    <div className="flex items-center gap-2 mb-4">
                      <div className={`h-8 w-8 rounded-lg flex items-center justify-center text-xs font-black uppercase ${STORE_CONFIG[p.source ?? '']?.bg} ${STORE_CONFIG[p.source ?? '']?.border} border text-white`}>
                        {p.source?.[0]}
                      </div>
                      <span className="text-xs font-black uppercase tracking-widest text-slate-400">{p.source}</span>
                    </div>

                    <h4 className="line-clamp-2 text-sm font-bold text-white leading-relaxed mb-4 group-hover:text-accent transition-colors">
                      {p.product_name}
                    </h4>

                    <div className="flex items-end justify-between">
                      <div>
                        <p className="text-[10px] font-black uppercase text-slate-500 tracking-[0.2em] mb-1">Live Price</p>
                        <p className="text-2xl font-black text-white">{p.currency} {p.price?.toLocaleString()}</p>
                      </div>
                      <div className="text-right">
                        <div className="flex items-center gap-1.5 justify-end">
                          <CheckCircle2 className={`h-3 w-3 ${ (p.title_match_score ?? 0) >= 0.7 ? 'text-emerald-400' : 'text-amber-400' }`} />
                          <span className={`text-[10px] font-black uppercase tracking-widest ${ (p.title_match_score ?? 0) >= 0.7 ? 'text-emerald-400' : 'text-amber-400' }`}>
                            {((p.title_match_score ?? 0) * 100).toFixed(0)}% Match
                          </span>
                        </div>
                      </div>
                    </div>

                    <a
                      href={p.product_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-white/5 py-3 text-[10px] font-black uppercase tracking-widest text-slate-200 transition hover:bg-accent hover:text-ink-950"
                    >
                      Visit Product Page <ArrowRight className="h-3 w-3" />
                    </a>
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Section 6: AI Rationale */}
        {genaiText && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-700">
            <div className="glass rounded-3xl p-8 border-l-4 border-l-accent overflow-hidden relative">
              <div className="absolute top-0 right-0 p-8 opacity-5">
                <Zap className="h-40 w-40 text-accent" />
              </div>
              <h3 className="text-lg font-bold text-white uppercase tracking-tight flex items-center gap-2 mb-6">
                <Zap className="h-5 w-5 text-accent" /> Intelligence Report
              </h3>
              <div className="prose prose-invert max-w-none">
                <div className="whitespace-pre-wrap font-medium text-sm leading-8 text-slate-300 first-letter:text-4xl first-letter:font-black first-letter:text-accent first-letter:mr-2">
                  {genaiText}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Section 7: History Trail */}
        {vision && (
          <div className="glass rounded-3xl p-8">
            <div className="flex items-center justify-between mb-8">
              <h3 className="text-lg font-bold text-white uppercase tracking-tight flex items-center gap-2">
                <HistoryIcon className="h-5 w-5 text-accent" /> Asset Valuation History
              </h3>
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Last 30 Days</div>
            </div>
            <History productHash={vision.product_hash} />
          </div>
        )}
      </main>

      <footer className="border-t border-white/5 bg-black/40 px-6 py-12 text-center relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_bottom,rgba(0,242,254,0.05),transparent)]" />
        <div className="relative mx-auto max-w-6xl">
          <div className="flex flex-col items-center gap-6">
            <div className="flex items-center gap-8 text-[10px] font-black uppercase tracking-[0.3em] text-slate-500">
              <span className="hover:text-accent transition-colors cursor-pointer">Security Protocol</span>
              <span className="hover:text-accent transition-colors cursor-pointer">Live Observability</span>
              <span className="hover:text-accent transition-colors cursor-pointer">API Integration</span>
            </div>
            <p className="text-[10px] font-bold uppercase tracking-widest text-slate-600">
              System powered by Prometheus & Grafana Intelligence Stack
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}

function History({ productHash }: { productHash: string }) {
  const [pts, setPts] = useState<{ source: string; price: number; scraped_at: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API}/api/v1/history/${productHash}?days=30`);
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json();
        if (!cancelled) setPts(j.points ?? []);
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [productHash]);

  if (err) return <div className="text-center py-10 text-xs font-black uppercase tracking-widest text-rose-400/60">{err}</div>;
  if (!pts.length)
    return <div className="text-center py-20 glass rounded-2xl text-xs font-black uppercase tracking-widest text-slate-600">New asset signature — no historical data available</div>;

  return (
    <div className="h-[240px] w-full">
      <ResponsiveContainer>
        <BarChart data={pts.map((p, i) => ({ i, price: p.price, source: p.source, color: STORE_CONFIG[p.source]?.color ?? '#fff' }))}>
          <CartesianGrid strokeDasharray="10 10" vertical={false} stroke="#ffffff08" />
          <XAxis dataKey="i" hide />
          <YAxis stroke="#ffffff20" fontSize={10} tick={{ fill: "#94a3b8" }} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ fill: '#ffffff05' }}
            contentStyle={{ background: "#0a0c10cc", border: "1px solid #ffffff10", borderRadius: '16px', backdropFilter: 'blur(10px)' }}
            itemStyle={{ fontWeight: '800', fontSize: '10px' }}
            labelClassName="hidden"
            formatter={(v: number) => `₹ ${v.toLocaleString()}`}
          />
          <Bar dataKey="price" radius={[4, 4, 0, 0]}>
            {pts.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={STORE_CONFIG[entry.source]?.color ?? '#94a3b8'} fillOpacity={0.4} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
