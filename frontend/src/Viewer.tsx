import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { Box, RotateCcw, ScanLine, Grid2X2 } from 'lucide-react';

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach((material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) value.dispose();
      });
      material.dispose();
    });
  });
}

export function Viewer({ url, label }: { url: string | null; label: string }) {
  const mount = useRef<HTMLDivElement>(null);
  const model = useRef<THREE.Object3D | null>(null);
  const reset = useRef<() => void>(() => {});
  const [wireframe, setWireframe] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState('');

  useEffect(() => {
    const host = mount.current;
    if (!host) return;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
    catch { setError('当前浏览器无法启用 WebGL，模型仍可下载到本地查看。'); return; }
    let disposed = false;
    setError(''); setStats(''); setWireframe(false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x171d1a, 1);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.25;
    host.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 1000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minDistance = 0.1;
    const resetCamera = () => {
      camera.position.set(3.3, 2.1, 3.8);
      controls.target.set(0, 0.9, 0);
      controls.update();
    };
    reset.current = resetCamera; resetCamera();
    scene.add(new THREE.HemisphereLight(0xffffff, 0x859785, 2.6));
    const key = new THREE.DirectionalLight(0xfff3dd, 4);
    key.position.set(4, 7, 5); scene.add(key);
    const fill = new THREE.DirectionalLight(0xb7d7ff, 2);
    fill.position.set(-4, 3, -2); scene.add(fill);
    const grid = new THREE.GridHelper(12, 24, 0x415a49, 0x2a372f);
    scene.add(grid);
    // The empty workspace marker is a viewport guide, never a generated asset.
    const guide = new THREE.Group();
    const edges = new THREE.EdgesGeometry(new THREE.BoxGeometry(1.1, 1.1, 1.1));
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0xa7c89b, transparent: true, opacity: 0.4 });
    const cube = new THREE.LineSegments(edges, lineMaterial);
    cube.position.y = 0.8; guide.add(cube);
    if (!url) scene.add(guide);

    const resize = new ResizeObserver(() => {
      const width = host.clientWidth, height = host.clientHeight;
      renderer.setSize(width, height);
      camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix();
    });
    resize.observe(host);
    if (url) {
      setLoading(true);
      const manager = new THREE.LoadingManager();
      manager.setURLModifier((resource) => {
        // Only self-contained GLB files are accepted; no arbitrary external resources.
        if (resource === url || resource.startsWith('blob:') || resource.startsWith('data:')) return resource;
        throw new Error('GLB 包含外部资源，请使用自包含模型');
      });
      new GLTFLoader(manager).load(url, (gltf) => {
        if (disposed) { disposeObject(gltf.scene); return; }
        const object = gltf.scene;
        const box = new THREE.Box3().setFromObject(object);
        const size = box.getSize(new THREE.Vector3());
        const scale = 2 / Math.max(size.x, size.y, size.z, 0.001);
        object.scale.multiplyScalar(scale);
        const scaledBox = new THREE.Box3().setFromObject(object);
        const center = scaledBox.getCenter(new THREE.Vector3());
        object.position.add(new THREE.Vector3(-center.x, -scaledBox.min.y, -center.z));
        model.current = object; scene.add(object);
        let triangles = 0;
        object.traverse((item) => {
          if (item instanceof THREE.Mesh) {
            triangles += (item.geometry.index?.count ?? item.geometry.attributes.position?.count ?? 0) / 3;
          }
        });
        setStats(`${Math.round(triangles).toLocaleString()} 三角面`);
        setLoading(false);
      }, undefined, () => {
        if (!disposed) { setLoading(false); setError('无法解析模型。请使用自包含 GLB；压缩扩展暂不支持。'); }
      });
    } else { setLoading(false); }
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
    return () => {
      disposed = true; resize.disconnect(); controls.dispose(); renderer.setAnimationLoop(null);
      if (model.current) { disposeObject(model.current); model.current = null; }
      edges.dispose(); lineMaterial.dispose();
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
      renderer.dispose(); renderer.forceContextLoss(); renderer.domElement.remove();
    };
  }, [url]);

  useEffect(() => {
    model.current?.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        const materials = Array.isArray(object.material) ? object.material : [object.material];
        materials.forEach((material) => {
          if ('wireframe' in material) material.wireframe = wireframe;
        });
      }
    });
  }, [wireframe]);

  return <div className="viewport">
    <div className="viewport-canvas" ref={mount} />
    <div className="viewport-top"><span><Box size={14} /> {label}</span><span>透视视图</span></div>
    {!url && !error && <div className="viewport-empty">
      <span className="eyebrow">三维工作台</span>
      <h2>让灵感，拥有形状。</h2>
      <p>上传角色与姿势参考，开始构建你的三维资产。</p>
      <span className="guide-note">空场景 · 等待模型</span>
    </div>}
    {(error || loading) && <div className="viewer-notice" role="status">{error || '正在载入模型…'}</div>}
    <div className="viewport-bottom"><span><Grid2X2 size={13} /> {stats || '3D 工作台'}</span>
      <span>拖拽旋转 · 滚轮缩放 · 右键平移</span></div>
    <div className="viewer-tools">
      <button title="重置视角" aria-label="重置视角" onClick={() => reset.current()}><RotateCcw size={17} /></button>
      <button title="线框模式" aria-label="线框模式" disabled={!url} className={wireframe ? 'active' : ''}
        onClick={() => setWireframe(!wireframe)}><ScanLine size={18} /></button>
    </div>
  </div>;
}
