import { createContext, useContext, useEffect, useState, type Dispatch, type ReactNode,
  type SetStateAction } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Asset, type Capabilities, type Job, type PoseMode } from './api';
import { useAccountSession } from './accountApi';
import { applyTheme, loadTheme, type Theme } from './theme';
import { hydrateLocalData, localAsset, localRecords } from './localData';
import { sessionToken } from './session';
import { configureCloudStorage, restoreCloudData } from './cloudStorage';
import { TERMINAL, acknowledge, saveJob, type LocalJob } from './transient';

/**
 * Everything the customer pages share.  It lives in one provider above the
 * routes so that walking between the workspace, the wardrobe pages and the
 * history keeps the form, the task list and the upload counter alive.
 */
export type CustomerWorkspace = {
  caps: Capabilities | null;
  jobs: LocalJob[];
  setJobs: Dispatch<SetStateAction<LocalJob[]>>;
  selected: string | null;
  account: ReturnType<typeof useAccountSession>;
  modelPrice: number | null;
  error: string;
  setError: Dispatch<SetStateAction<string>>;
  theme: Theme;
  setTheme: Dispatch<SetStateAction<Theme>>;
  name: string;
  setName: Dispatch<SetStateAction<string>>;
  front?: Asset;
  setFront: Dispatch<SetStateAction<Asset | undefined>>;
  reference?: Asset;
  setReference: Dispatch<SetStateAction<Asset | undefined>>;
  views: Record<string, Asset | undefined>;
  setViews: Dispatch<SetStateAction<Record<string, Asset | undefined>>>;
  viewsConsistent: boolean;
  setViewsConsistent: Dispatch<SetStateAction<boolean>>;
  poseMode: PoseMode;
  changePose: (mode: PoseMode) => void;
  background: boolean;
  setBackground: Dispatch<SetStateAction<boolean>>;
  topology: boolean;
  setTopology: Dispatch<SetStateAction<boolean>>;
  texture: boolean;
  setTexture: Dispatch<SetStateAction<boolean>>;
  rig: boolean;
  setRig: Dispatch<SetStateAction<boolean>>;
  neutral: boolean;
  setNeutral: Dispatch<SetStateAction<boolean>>;
  fbx: boolean;
  setFbx: Dispatch<SetStateAction<boolean>>;
  faceCount: number;
  setFaceCount: Dispatch<SetStateAction<number>>;
  faceLevel: string;
  setFaceLevel: Dispatch<SetStateAction<string>>;
  polygon: string;
  setPolygon: Dispatch<SetStateAction<string>>;
  uploadCount: number;
  setUploadCount: Dispatch<SetStateAction<number>>;
  localModel: { url: string; name: string } | null;
  setLocalModel: Dispatch<SetStateAction<{ url: string; name: string } | null>>;
  artifact: string | null;
  setArtifact: Dispatch<SetStateAction<string | null>>;
  ready: boolean;
  addJob: (job: LocalJob) => void;
  chooseJob: (item: LocalJob) => void;
  newProject: () => void;
  previewImage: (id?: string | null) => string | undefined;
};

const CustomerContext = createContext<CustomerWorkspace | null>(null);

/** The shared customer workspace; only valid under the customer layout. */
export function useCustomer(): CustomerWorkspace {
  const value = useContext(CustomerContext);
  if (!value) throw new Error('useCustomer must be called inside CustomerLayout');
  return value;
}

export function CustomerProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [jobs, setJobs] = useState<LocalJob[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const account = useAccountSession();
  const [modelPrice, setModelPrice] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [name, setName] = useState('');
  const [front, setFront] = useState<Asset>();
  const [reference, setReference] = useState<Asset>();
  const [views, setViews] = useState<Record<string, Asset | undefined>>({});
  const [viewsConsistent, setViewsConsistent] = useState(false);
  const [poseMode, setPoseMode] = useState<PoseMode>('original');
  const [background, setBackground] = useState(false);
  const [topology, setTopology] = useState(false);
  const [texture, setTexture] = useState(true);
  const [rig, setRig] = useState(false);
  const [neutral, setNeutral] = useState(false);
  const [fbx, setFbx] = useState(false);
  const [faceCount, setFaceCount] = useState(100000);
  const [faceLevel, setFaceLevel] = useState('medium');
  const [polygon, setPolygon] = useState('triangle');
  const [uploadCount, setUploadCount] = useState(0);
  const [localModel, setLocalModel] = useState<{ url: string; name: string } | null>(null);
  const [artifact, setArtifact] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const capabilities = await api<Capabilities>('/api/capabilities');
        configureCloudStorage(capabilities.private_storage === true);
        // This browser's own record of every task it started.
        const saved = await localRecords<LocalJob>('job:');
        const merged = new Map(saved.map((item) => [item.id, item]));
        if (sessionToken.read()) {
          // Tasks the server still holds: save their files, then let it delete
          // its copies.  A task whose files could not be saved stays on the
          // server and is retried on the next tick.
          for (const live of await api<Job[]>('/api/jobs')) {
            // Every artifact is saved here first; only then may the server
            // forget its own copy.
            const mirror = await saveJob(live);
            merged.set(mirror.id, mirror);
            if (TERMINAL.has(live.state)) await acknowledge('jobs', live.id);
          }
        }
        if (!stopped) {
          setCaps(capabilities);
          setJobs([...merged.values()].sort((left, right) => right.created - left.created));
        }
      } catch (err) { if (!stopped) setError(`连接工作台失败：${(err as Error).message}`); }
      finally { if (!stopped) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { stopped = true; clearTimeout(timer); };
  }, [account.user?.id]);
  useEffect(() => {
    // The browser keeps one data partition per account; switching re-reads it.
    let active = true;
    setReady(false);
    if (account.checking) return;
    void api<Capabilities>('/api/capabilities').then(async (capabilities) => {
      configureCloudStorage(capabilities.private_storage === true);
      await hydrateLocalData();
      await restoreCloudData();
    })
      .then(() => { if (active) setReady(true); })
      .catch((err) => { if (active) { setReady(true); setError((err as Error).message); } });
    return () => { active = false; };
  }, [account.user?.id, account.checking]);
  useEffect(() => {
    let alive = true;
    void api<{ model_price_points: number }>('/api/pricing').then((pricing) => {
      if (alive) setModelPrice(pricing.model_price_points);
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  useEffect(() => { applyTheme(theme); }, [theme]);

  function addJob(item: LocalJob) {
    setJobs((list) => [item, ...list.filter((existing) => existing.id !== item.id)]);
  }
  function chooseJob(item: LocalJob) {
    setSelected(item.id); setArtifact(null); setLocalModel(null);
  }
  function newProject() {
    setSelected(null); setArtifact(null); setLocalModel(null);
    setName(''); setFront(undefined); setReference(undefined); setViews({}); setViewsConsistent(false);
    setPoseMode('original'); setRig(false); setNeutral(false); setError('');
    navigate('/');
  }
  function changePose(mode: PoseMode) {
    setPoseMode(mode);
    if (mode !== 'original') setViews({});
    if (mode !== 'custom') setReference(undefined);
    if (mode === 'custom') { setRig(false); setNeutral(false); }
  }
  const previewImage = (id?: string | null) => (ready && id ? localAsset(id)?.url : undefined);

  return <CustomerContext.Provider value={{
    caps, jobs, setJobs, selected, account, modelPrice, error, setError,
    theme, setTheme,
    name, setName, front, setFront, reference, setReference, views, setViews,
    viewsConsistent, setViewsConsistent, poseMode, changePose,
    background, setBackground, topology, setTopology, texture, setTexture,
    rig, setRig, neutral, setNeutral, fbx, setFbx,
    faceCount, setFaceCount, faceLevel, setFaceLevel, polygon, setPolygon,
    uploadCount, setUploadCount, localModel, setLocalModel, artifact, setArtifact, ready,
    addJob, chooseJob, newProject, previewImage,
  }}>{children}</CustomerContext.Provider>;
}
