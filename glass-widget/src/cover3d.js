import * as THREE from './vendor/three.module.js';

const container = document.querySelector('#cover');
const scene = new THREE.Scene();
const camera = new THREE.OrthographicCamera(-1.25, 1.25, 1.25, -1.25, 0.1, 20);
camera.position.z = 5;
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const orbit = new THREE.Group();
scene.add(orbit);
const disc = new THREE.Group();
orbit.add(disc);
const halfThickness = 0.0125;
const holeRadius = 0.025;
const recordShape = new THREE.Shape();
recordShape.absarc(0, 0, 1, 0, Math.PI * 2, false);
const centerHole = new THREE.Path();
centerHole.absarc(0, 0, holeRadius, 0, Math.PI * 2, true);
recordShape.holes.push(centerHole);
const bodyGeometry = new THREE.ExtrudeGeometry(recordShape, { depth: halfThickness * 2, bevelEnabled: false, curveSegments: 96 });
bodyGeometry.translate(0, 0, -halfThickness);
const body = new THREE.Mesh(
  bodyGeometry,
  new THREE.MeshPhysicalMaterial({ color: 0x16191e, metalness: 0.12, roughness: 0.32, clearcoat: 0.55, clearcoatRoughness: 0.25 })
);
disc.add(body);
const coverMaterial = new THREE.MeshBasicMaterial({ color: 0x59636e });
const face = new THREE.Mesh(new THREE.RingGeometry(holeRadius, 0.985, 96), coverMaterial);
face.position.z = halfThickness + 0.001;
disc.add(face);
const back = new THREE.Mesh(face.geometry, coverMaterial);
back.position.z = -(halfThickness + 0.001);
back.rotation.y = Math.PI;
disc.add(back);

// A narrow dark rim frames the artwork on both sides.
const rimGeometry = new THREE.RingGeometry(0.948, 0.985, 96);
const rimMaterial = new THREE.MeshStandardMaterial({ color: 0x080a0d, roughness: 0.4, metalness: 0.12 });
for (const side of [1, -1]) {
  const rim = new THREE.Mesh(rimGeometry, rimMaterial);
  rim.position.z = side * (halfThickness + 0.0015);
  if (side < 0) rim.rotation.y = Math.PI;
  disc.add(rim);
}

// Gold center label, a slim black surround, and a subtle warm halo.
const goldMaterial = new THREE.MeshPhysicalMaterial({
  color: 0xffd34d, metalness: 0.55, roughness: 0.15,
  clearcoat: 1, clearcoatRoughness: 0.08, emissive: 0xb87800, emissiveIntensity: 0.4
});
const goldOuterRadius = 0.17 * 4 / 3;
const blackOuterRadius = goldOuterRadius + 0.05;
const haloOuterRadius = blackOuterRadius + 0.035;
const goldGeometry = new THREE.RingGeometry(holeRadius, goldOuterRadius, 96);
const centerRimGeometry = new THREE.RingGeometry(goldOuterRadius, blackOuterRadius, 96);
const accentOuterRadius = goldOuterRadius - 0.012;
const accentWidth = (blackOuterRadius - goldOuterRadius) / 2;
const accentGeometry = new THREE.RingGeometry(accentOuterRadius - accentWidth, accentOuterRadius, 72, 1, Math.PI / 4, Math.PI * 1.5);
const accentMaterial = new THREE.MeshBasicMaterial({ color: 0xfff9ee });
const haloGeometry = new THREE.RingGeometry(blackOuterRadius, haloOuterRadius, 96);
const haloMaterial = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false,
  uniforms: { innerRadius: { value: blackOuterRadius }, outerRadius: { value: haloOuterRadius } },
  vertexShader: `varying vec2 p; void main() { p = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `uniform float innerRadius; uniform float outerRadius; varying vec2 p; void main() { float a = 0.16 * (1.0 - smoothstep(innerRadius, outerRadius, length(p))); gl_FragColor = vec4(1.0, 0.68, 0.16, a); }`
});
// A soft reflected light strip changes with the record's orientation.
const goldGlintMaterial = new THREE.ShaderMaterial({
  transparent: true, depthWrite: false,
  vertexShader: `
    varying vec3 viewPosition;
    varying vec3 viewNormal;
    void main() {
      vec4 pos = modelViewMatrix * vec4(position, 1.0);
      viewPosition = pos.xyz;
      viewNormal = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * pos;
    }
  `,
  fragmentShader: `
    varying vec3 viewPosition;
    varying vec3 viewNormal;
    void main() {
      vec3 halfDirection = normalize(normalize(vec3(-1.5, 2.0, 3.0) - viewPosition) + vec3(0.0, 0.0, 1.0));
      float facingLight = pow(max(dot(normalize(viewNormal), halfDirection), 0.0), 2.0);
      float stripe = exp(-pow((viewPosition.x + viewPosition.y * 0.55 - 0.025) * 9.0, 2.0));
      gl_FragColor = vec4(1.0, 0.94, 0.68, facingLight * (0.10 + 0.45 * stripe));
    }
  `
});
for (const side of [1, -1]) {
  for (const [geometry, material] of [[goldGeometry, goldMaterial], [centerRimGeometry, rimMaterial], [haloGeometry, haloMaterial]]) {
    const part = new THREE.Mesh(geometry, material);
    part.position.z = side * (halfThickness + 0.0025);
    if (side < 0) part.rotation.y = Math.PI;
    disc.add(part);
  }
  const glint = new THREE.Mesh(goldGeometry, goldGlintMaterial);
  glint.position.z = side * (halfThickness + 0.003);
  if (side < 0) glint.rotation.y = Math.PI;
  disc.add(glint);
  const accent = new THREE.Mesh(accentGeometry, accentMaterial);
  accent.position.z = side * (halfThickness + 0.0035);
  if (side < 0) accent.rotation.y = Math.PI;
  disc.add(accent);
}

// Subtle radial microgrooves catch light above the artwork on both sides.
const sheenMaterial = new THREE.ShaderMaterial({
  transparent: true,
  depthWrite: false,
  vertexShader: `
    varying vec3 viewPosition;
    varying vec3 viewNormal;
    varying vec2 discPosition;
    varying vec3 discX;
    varying vec3 discY;
    void main() {
      vec4 positionInView = modelViewMatrix * vec4(position, 1.0);
      viewPosition = positionInView.xyz;
      viewNormal = normalize(normalMatrix * normal);
      discPosition = position.xy;
      discX = normalize(normalMatrix * vec3(1.0, 0.0, 0.0));
      discY = normalize(normalMatrix * vec3(0.0, 1.0, 0.0));
      gl_Position = projectionMatrix * positionInView;
    }
  `,
  fragmentShader: `
    varying vec3 viewPosition;
    varying vec3 viewNormal;
    varying vec2 discPosition;
    varying vec3 discX;
    varying vec3 discY;
    float grooveBand(float radius, float center) {
      float edge = max(fwidth(radius), 0.001);
      return 1.0 - smoothstep(0.002, 0.002 + edge, abs(radius - center));
    }
    float grooves(float radius) {
      return max(max(max(grooveBand(radius, 0.45), grooveBand(radius, 0.52)),
                     max(grooveBand(radius, 0.61), grooveBand(radius, 0.68))),
                 max(grooveBand(radius, 0.79), grooveBand(radius, 0.89)));
    }
    void main() {
      vec3 normal = normalize(viewNormal);
      vec3 viewDirection = vec3(0.0, 0.0, 1.0);
      vec3 lightDirection = normalize(vec3(-1.5, 2.0, 3.0) - viewPosition);
      vec3 halfDirection = normalize(lightDirection + viewDirection);
      float radius = length(discPosition);
      vec2 radial = discPosition / max(radius, 0.001);
      vec3 radialDirection = normalize(radial.x * discX + radial.y * discY + vec3(0.00001));
      // Six fine grooves, spaced irregularly around a smooth center.
      float groove = grooves(radius);
      float slope = (grooves(radius + 0.008) - grooves(radius - 0.008)) * 0.5;
      vec3 grooveNormal = normalize(normal + radialDirection * slope * 0.22);
      float highlight = pow(max(dot(grooveNormal, halfDirection), 0.0), 24.0);
      float grazing = 1.0 - max(dot(normal, viewDirection), 0.0);
      float grooveLight = slope * dot(radialDirection, halfDirection) * (0.16 + grazing * 0.35);
      float shine = min(0.2, highlight * 0.10 + pow(grazing, 4.0) * 0.07 + max(grooveLight, 0.0));
      float shade = min(0.10, max(-grooveLight, 0.0));
      float grayLine = groove * 0.20;
      float opacity = shine + shade + grayLine;
      gl_FragColor = vec4(vec3((shine + grayLine * 0.65) / max(opacity, 0.0001)), opacity);
    }
  `
});
for (const side of [1, -1]) {
  const sheen = new THREE.Mesh(face.geometry, sheenMaterial);
  sheen.position.z = side * (halfThickness + 0.002);
  if (side < 0) sheen.rotation.y = Math.PI;
  disc.add(sheen);
}
scene.add(new THREE.HemisphereLight(0xffffff, 0x48505c, 2));
const light = new THREE.DirectionalLight(0xffffff, 2);
light.position.set(-2, 3, 5);
scene.add(light);
disc.rotation.set(-Math.PI / 4, 0, 0);

function render() { if (!document.hidden) renderer.render(scene, camera); }

// Rotate around the disc's own center and normal, independently of its dragged tilt.
const rotationPeriodSeconds = 30;
const orbitPeriodSeconds = 70;
const orbitAxis = new THREE.Vector3(Math.SQRT1_2, Math.SQRT1_2, 0);
let lastFrameTime = null;
let frameRequest = null;
let nextFrameAt = 0;
const frameInterval = 1000 / 10;
function animate(time) {
  frameRequest = null;
  if (document.hidden) return;
  if (time + 0.01 >= nextFrameAt) {
    if (lastFrameTime !== null) {
      const elapsed = Math.min((time - lastFrameTime) / 1000, 0.25);
      disc.rotateZ(-elapsed * Math.PI * 2 / rotationPeriodSeconds);
      orbit.rotateOnWorldAxis(orbitAxis, elapsed * Math.PI * 2 / orbitPeriodSeconds);
    }
    lastFrameTime = time;
    nextFrameAt += (Math.floor((time - nextFrameAt + 0.01) / frameInterval) + 1) * frameInterval;
    render();
  }
  frameRequest = requestAnimationFrame(animate);
}
function updateAnimationVisibility() {
  if (frameRequest !== null) cancelAnimationFrame(frameRequest);
  frameRequest = null;
  lastFrameTime = null;
  nextFrameAt = 0;
  if (!document.hidden) frameRequest = requestAnimationFrame(animate);
}
document.addEventListener('visibilitychange', updateAnimationVisibility);
updateAnimationVisibility();

function resize() {
  const { width, height } = container.getBoundingClientRect();
  if (!width || !height) return;
  renderer.setSize(width, height, false);
  const aspect = width / height;
  camera.left = -1.25 * aspect;
  camera.right = 1.25 * aspect;
  camera.updateProjectionMatrix();
  render();
}
new ResizeObserver(resize).observe(container);

let pointer = null;
container.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  pointer = { id: event.pointerId, x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false };
  container.setPointerCapture(event.pointerId);
  container.classList.add('rotating');
});
container.addEventListener('pointermove', event => {
  if (!pointer || pointer.id !== event.pointerId) return;
  if (Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY) > 4) pointer.moved = true;
  if (!pointer.moved) return;
  const dx = event.clientX - pointer.x;
  const dy = event.clientY - pointer.y;
  pointer.x = event.clientX;
  pointer.y = event.clientY;
  const rotation = new THREE.Quaternion().setFromEuler(new THREE.Euler(dy * 0.012, dx * 0.012, 0, 'XYZ'));
  // Convert the drag from screen axes into the orbit group's coordinate frame.
  const localRotation = orbit.quaternion.clone().invert().multiply(rotation).multiply(orbit.quaternion);
  disc.quaternion.premultiply(localRotation);
  render();
});
function endPointer(event) {
  if (pointer && pointer.id === event.pointerId) {
    const clicked = event.type === 'pointerup' && !pointer.moved;
    pointer = null;
    container.classList.remove('rotating');
    if (clicked) window.dispatchEvent(new Event('open-color-panel')); 
  }
}
container.addEventListener('pointerup', endPointer);
container.addEventListener('pointercancel', endPointer);
container.addEventListener('lostpointercapture', endPointer);
container.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); window.dispatchEvent(new Event('open-color-panel')); }
});

let generation = 0;
let currentTexture = null;
/** Set a cover URL (CORS-enabled), data URL, or blob URL. Empty value clears it. */
async function setCover(url) {
  const request = ++generation;
  if (!url) {
    coverMaterial.map = null;
    coverMaterial.color.set(0x59636e);
    coverMaterial.needsUpdate = true;
    currentTexture?.dispose();
    currentTexture = null;
    render();
    return;
  }
  const texture = await new THREE.TextureLoader().loadAsync(url);
  if (request !== generation) { texture.dispose(); return; }
  texture.colorSpace = THREE.SRGBColorSpace;
  const { width, height } = texture.image;
  if (width > height) texture.repeat.x = height / width;
  else if (height > width) texture.repeat.y = width / height;
  texture.offset.set((1 - texture.repeat.x) / 2, (1 - texture.repeat.y) / 2);
  const old = currentTexture;
  currentTexture = texture;
  coverMaterial.map = texture;
  coverMaterial.color.set(0xffffff);
  coverMaterial.needsUpdate = true;
  old?.dispose();
  render();
}

// CSS length or percentage; sizing remains independent from model geometry.
function setSize(size) {
  if (!CSS.supports('width', size)) throw new TypeError('封面尺寸必须是有效的 CSS 宽度');
  container.style.setProperty('--cover-size', size);
}
window.cover3D = Object.freeze({ setCover, setSize });
window.dispatchEvent(new Event('cover3d-ready'));
resize();












