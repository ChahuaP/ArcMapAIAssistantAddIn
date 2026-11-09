import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

export async function mountAvatar(container, mouthLevel, state) {
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'low-power' });
    const canvas = renderer.domElement;
    canvas.setAttribute('aria-hidden', 'true');
    container.append(canvas);
    renderer.setPixelRatio(Math.min(1.6, devicePixelRatio));
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 12);
    scene.add(new THREE.HemisphereLight(0xf5fafa, 0x7b9ea7, 2));
    const key = new THREE.DirectionalLight(0xfff6e8, 1.8);
    key.position.set(-2, 4, 5);
    scene.add(key);
    const loader = new GLTFLoader();
    loader.register(parser => new VRMLoaderPlugin(parser));
    const loaded = await loader.loadAsync('/avatar.vrm');
    const vrm = loaded.userData.vrm;
    if (!vrm?.expressionManager || !vrm.humanoid) throw new Error('数字形象资源缺少口型或骨骼');
    VRMUtils.rotateVRM0(vrm);
    VRMUtils.removeUnnecessaryVertices(vrm.scene);
    const colored = new Set();
    vrm.scene.traverse(object => {
        for (const material of (Array.isArray(object.material) ? object.material : [object.material])) {
            if (!material?.color || colored.has(material)) continue;
            colored.add(material);
            if (material.name.includes('_HAIR')) material.color.multiply(new THREE.Color(0x677d89));
            if (material.name.includes('_Tops_')) material.color.multiply(new THREE.Color(0x668d9a));
        }
    });
    scene.add(vrm.scene);
    scene.updateMatrixWorld(true);
    const bone = name => vrm.humanoid.getNormalizedBoneNode(name);
    const head = bone('head');
    const chest = bone('chest');
    const position = head.getWorldPosition(new THREE.Vector3());
    const center = position.y - 0.095;
    camera.position.set(0, center, 3);
    camera.lookAt(0, center, 0);
    bone('leftUpperArm').rotation.z = 1.20;
    bone('rightUpperArm').rotation.z = -1.20;

    function resize() {
        const width = container.clientWidth;
        const height = container.clientHeight;
        if (!width || !height) return;
        const half = 0.36;
        camera.left = -half * width / height;
        camera.right = half * width / height;
        camera.top = half;
        camera.bottom = -half;
        camera.updateProjectionMatrix();
        renderer.setSize(width, height, false);
    }
    new ResizeObserver(resize).observe(container);
    resize();
    const time = performance.now();
    let last = 0;
    let mouth = 0;
    function animate(now) {
        requestAnimationFrame(animate);
        if (document.hidden || now - last < 31) return;
        const delta = last ? Math.min(0.08, (now - last) / 1000) : 0.031;
        last = now;
        const tick = (now - time) / 1000;
        const current = state();
        const breathe = current.reduced ? 0 : Math.sin(tick * 1.7);
        const tilt = current.voice === 'listening' ? 0.065 : current.task === 'running' ? -0.055 : 0;
        head.rotation.z += (tilt + breathe * 0.018 - head.rotation.z) * 0.1;
        head.rotation.y = breathe * 0.035;
        if (chest) chest.rotation.x = breathe * 0.013;
        const blinkTime = (tick + 1.8) % 4.2;
        vrm.expressionManager.setValue('blink', current.reduced || blinkTime > 0.18 ? 0 : Math.sin(blinkTime / 0.18 * Math.PI));
        mouth += (mouthLevel() - mouth) * 0.37;
        vrm.expressionManager.setValue('aa', mouth);
        vrm.update(delta);
        renderer.render(scene, camera);
    }
    canvas.dataset.ready = 'true';
    requestAnimationFrame(animate);
}
