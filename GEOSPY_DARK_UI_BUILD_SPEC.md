# GeoSpy-style Dark Marketing UI — Reconstruction Specification

## 1. Purpose and evidence

This document describes how to reproduce the **layout system, dark visual language, scroll choreography, component behavior, and animation character** visible in the supplied screen recording. It is a build plan only; no website implementation is included.

Analyzed source:

- Recording: `20261018-1233-36.1894260.mp4`
- Resolution: **1910 × 970**
- Frame rate: **30 fps**
- Duration: **15.97 seconds**
- Observed state: desktop, dark theme, continuous downward scroll

The current live page was also inspected to obtain measurable typography and geometry. Values marked **measured** came from the live DOM at a 1280 × 720 viewport. Values marked **estimated** were inferred from the supplied recording and should be tuned against screenshots during implementation.

Do not copy GeoSpy trademarks, protected copy, photographs, or proprietary videos. Recreate the interaction system with original branding and licensed/original media.

---

## 2. What makes the design feel premium

The page is deliberately restrained. Its quality comes from proportion and movement rather than decoration:

1. Almost-black space surrounds every important object.
2. Each viewport contains one dominant idea.
3. Headings are large but not excessively heavy.
4. Buttons are small, bright, and visually precise.
5. Media appears inside carefully masked geometric containers.
6. Scroll motion connects sections instead of treating them as unrelated blocks.
7. Card surfaces are only slightly brighter than the background.
8. Rounded corners are moderate; the interface does not use inflated “bubble” styling.
9. Animation is slow, smooth, and scroll-controlled rather than constantly moving.
10. Navigation remains stable while the content moves beneath it.

---

## 3. Page architecture

```text
App
├── StickyHeader
│   ├── Brand
│   ├── PrimaryNavigation
│   └── HeaderActions
├── HeroSequence
│   ├── ConcentricRingField
│   ├── PlayControl
│   ├── HeroCopy
│   ├── PrimaryCTA
│   ├── TrustLine
│   └── RisingProductWindow
│       └── ProductDemoMedia
├── EnterpriseIntro
│   ├── Eyebrow
│   ├── Heading
│   ├── SupportingCopy
│   └── FeatureTabs
├── ProductFeatureCard
│   ├── CopyColumn
│   └── MediaColumn
├── EditorialBanner
├── IntelligenceSection
│   ├── CopyColumn
│   └── CaseStudyCard
├── BlogSection
│   ├── SectionIntro
│   └── ArticleCardGrid
└── Footer
    ├── BrandAndSocials
    ├── NewsletterForm
    └── LegalRow
```

Use semantic sections even if the visual design is canvas-like. The scroll effects should enhance normal document flow, not replace it.

---

## 4. Global visual system

### 4.1 Color tokens

Recommended reconstruction tokens:

```css
:root {
  --bg: #050506;
  --bg-elevated: #0b0b0d;
  --surface-1: #111214;
  --surface-2: #17181b;
  --surface-hover: #1d1f22;
  --line: rgba(255, 255, 255, 0.09);
  --line-strong: rgba(255, 255, 255, 0.16);
  --text: rgba(255, 255, 255, 0.96);
  --text-soft: rgba(255, 255, 255, 0.70);
  --text-muted: rgba(255, 255, 255, 0.45);
  --button-bg: #f4f4f1;
  --button-text: #0a0a0b;
  --shadow: 0 28px 90px rgba(0, 0, 0, 0.48);
}
```

The recording uses neutral blacks rather than blue-black cybersecurity colors. Avoid neon green, glowing borders, excessive grid overlays, and high-saturation gradients.

### 4.2 Typography

The live page uses **Plus Jakarta Sans** for primary headings and **Inter** for compact interface text.

Measured desktop values:

| Role | Size | Line height | Weight | Tracking |
|---|---:|---:|---:|---:|
| Hero H1 | 60px | 72px | 600 | -1.8px |
| Section H2 | 48px | 67.2px | 600 | -1.92px |
| Editorial H4 | 30px | 42px | 600 | -1.2px |
| Article H5 | 24px | 33.6px | 600 | -0.72px |
| Compact UI/button | 12–15px | 1.2–1.4 | 500–600 | 0 to 0.04em |

Recommended responsive scale:

```css
.hero-title { font-size: clamp(3rem, 4.7vw, 5rem); }
.section-title { font-size: clamp(2.25rem, 3.75vw, 3.25rem); }
.card-title { font-size: clamp(1.5rem, 2.2vw, 2rem); }
```

Typography rules:

- Use sentence case, not uppercase, for all major headings.
- Keep headings between 10 and 18 words where possible.
- Apply negative tracking only to display headings.
- Use muted gray for paragraphs; pure white is reserved for headings.
- Constrain paragraph width to roughly 50–65 characters.

### 4.3 Container and spacing system

- Maximum content width: **1180–1240px estimated**.
- Page side padding: **48–64px desktop**, 24px tablet, 16px mobile.
- Navigation height: **approximately 64px in the recording**. The current live implementation measures 90px, but the recorded dark build is visually tighter.
- Major vertical section gap: **140–220px estimated**.
- Card padding: **40–56px desktop**, 24px mobile.
- Common radii observed on the current site: 3px, 7–10px, 14–20px, and fully rounded masks.
- Primary CTA height: **40–50px measured**.

Do not use the same radius everywhere. Buttons are compact (3–8px); large media cards use 14–20px; circular artwork uses 50%.

---

## 5. Header specification

### Structure

- Sticky/fixed horizontal header.
- Brand at far left.
- Three compact navigation items near the brand.
- Right cluster: theme icon, search icon, and high-contrast CTA.
- Transparent or nearly black background at the top.
- A subtle darkened backdrop appears as the page scrolls.

### Styling

```css
.site-header {
  position: fixed;
  inset: 0 0 auto;
  z-index: 100;
  height: 64px;
  background: rgba(5, 5, 6, 0.78);
  border-bottom: 1px solid transparent;
  backdrop-filter: blur(16px);
  transition: background-color 300ms ease, border-color 300ms ease;
}

.site-header.is-scrolled {
  background: rgba(5, 5, 6, 0.94);
  border-color: rgba(255, 255, 255, 0.08);
}
```

### Motion

- Header itself does not bounce or scale.
- Theme/search icons use a 160–220ms opacity/color transition.
- CTA hover: translate upward 1px and slightly darken the white surface.
- Mobile navigation should become a compact menu rather than wrapping.

---

## 6. Hero sequence

The hero is the most important part of the reconstruction.

### 6.1 Composition

- Full-width, nearly full-viewport opening section.
- Centered copy stack.
- Small circular play control above the title.
- Large two-line heading.
- One-line subtitle.
- Compact white CTA.
- Small trust/proof line below the CTA.
- Faint concentric ellipses/rings behind everything.
- A large product-window mockup begins below the fold and rises into view during scroll.

### 6.2 Ring field

The background contains very low-contrast elliptical rings. They should feel architectural, not like a radar animation.

Implementation options:

```css
.ring-field::before,
.ring-field::after {
  content: "";
  position: absolute;
  left: 50%;
  top: 46%;
  width: min(90vw, 1180px);
  aspect-ratio: 1.45;
  transform: translate(-50%, -50%);
  border: 1px solid rgba(255, 255, 255, 0.055);
  border-radius: 50%;
}
```

Add four to six rings using nested elements or repeating radial gradients. The rings should not spin. A tiny scroll-linked scale change (about 1.00 to 1.06) is enough.

### 6.3 Hero entrance

On initial load:

1. Rings fade in over 700–900ms.
2. Play control fades and rises 10px.
3. Heading rises 18–24px and fades in over 650–800ms.
4. Subtitle appears 80–120ms later.
5. CTA and proof line appear last.

Recommended easing:

```css
--ease-premium: cubic-bezier(.22, 1, .36, 1);
```

Avoid springy overshoot. The recording uses controlled deceleration.

### 6.4 Scroll-linked product-window reveal

Between approximately **2.0s and 5.5s** of the recording, the browser/product window rises from below the fold.

Starting state:

- `translateY(180–260px)`
- `scale(0.84–0.90)`
- `rotateX(8–13deg)` with the top edge visually farther away
- opacity around 0.85

Ending state:

- `translateY(0)`
- `scale(1)`
- `rotateX(0deg)`
- opacity 1

Use a sticky hero track so this transformation is driven by scroll progress rather than a one-time entrance.

```js
progress = clamp((scrollY - heroStart) / heroTravel, 0, 1)
y = lerp(220, 0, easeOut(progress))
scale = lerp(0.87, 1, progress)
rotateX = lerp(11, 0, progress)
```

The product window should use `transform-origin: 50% 100%` or a carefully tuned center-bottom origin.

### 6.5 Product demo media

The media changes visually as the scroll continues:

- Initial frame: dark map/product view inside a desktop browser shell.
- Following frames: source image becomes dominant.
- Image is masked with a large circular/oval crop.
- The crop and media move more slowly than the page, producing subtle parallax.

Implementation choices:

1. Use a pre-rendered product demo video inside the mockup for exact fidelity.
2. Or create a scroll-scrubbed frame sequence/canvas.
3. Or crossfade two still media layers based on scroll progress.

For this project, a lightweight two-layer crossfade is sufficient unless the exact recorded product animation is required.

```css
.demo-layer {
  position: absolute;
  inset: 0;
  transition: opacity 500ms var(--ease-premium);
}

.portrait-mask {
  clip-path: ellipse(31% 44% at 50% 54%);
}
```

---

## 7. Enterprise introduction

Visible around **6–8 seconds**.

### Layout

- Large area of empty black space before the text.
- Small centered eyebrow label.
- Centered two-line H2.
- Centered paragraph with a narrow measure.
- Horizontal pill-style feature selector underneath.

### Feature selector

- Three items: one active, two inactive.
- Dark pill surfaces with 1px borders.
- Active item has brighter text and surface.
- Small icon precedes the text.

Interaction:

- 180–240ms background/border transition.
- Switching tabs crossfades the feature card content.
- Card media may translate 8–12px during the swap.
- Maintain the same card height to prevent layout jumps.

---

## 8. Product feature card

Visible around **8–10 seconds**.

### Layout

- Wide elevated rectangle centered within the content container.
- Two columns, approximately **48/52** or **45/55**.
- Copy on the left; photo/media on the right.
- Surface is #111–#171 range, only slightly brighter than the page.
- Media has an inset border radius and fills most of its column.

### Copy column

- Small category label.
- Large two-line heading.
- Short supporting paragraph.
- Compact light CTA.

### Media behavior

- `object-fit: cover`.
- Initial image scale 1.00.
- Hover scale 1.025–1.04 over 600–800ms.
- Overflow hidden at the card boundary.

### Scroll entrance

- Card opacity 0 → 1.
- Card y-position 42px → 0.
- Media y-position 24px → 0 with a slightly delayed start.
- Duration equivalent: 700–900ms if triggered, or 15–20vh if scrubbed.

---

## 9. Editorial banner

This dark image-led panel acts as a visual break after the feature card.

- Wide rectangular media panel.
- Very dark monochrome/desaturated photography.
- Text aligns toward the lower-left or center-left.
- A small brand mark/eyebrow sits above the title.
- Optional compact status pill near the top-right.
- Use a heavy black gradient overlay to guarantee text contrast.

Animation:

- Image parallax range: about 5–8% of the container height.
- Text enters upward 20–28px.
- Avoid separate animations for every line; reveal the copy as one group.

---

## 10. “Turn pixels into intelligence” section

Visible around **10–12 seconds**.

### Composition

- Asymmetric split layout.
- Left: H2, paragraph, and white CTA.
- Right: tall case-study image/card.
- Black negative space separates both sides.

### Motion

- Left copy remains relatively stable.
- Right image enters from 24–40px below.
- Image continues a slight upward parallax during scrolling.
- Case-study caption sits inside a bottom gradient rather than outside the image.

Desktop grid:

```css
.intelligence-grid {
  display: grid;
  grid-template-columns: minmax(0, .9fr) minmax(420px, 1.1fr);
  gap: clamp(48px, 7vw, 110px);
  align-items: center;
}
```

---

## 11. Blog section

Visible around **12–15 seconds**.

### Section heading

- Centered eyebrow.
- Centered H2.
- One-line description.
- Compact outline/text CTA.

### Card grid

- Three equal columns on desktop.
- Tall cards with an image occupying roughly the top 55–62%.
- Dark surface and subtle border.
- Small category pill overlays the image near the top-right.
- Title and metadata align at the bottom.

### Card motion

- Stagger cards by 70–110ms.
- Initial state: opacity 0, translateY(30px).
- Hover: card rises 4px; image scales 1.03; border becomes slightly brighter.
- Use a 350–500ms premium easing—not a fast 150ms dashboard hover.

---

## 12. Footer

Visible around **15–16 seconds**.

### Layout

- Large top padding and generous black space.
- Two principal columns:
  - brand, short tagline, social links;
  - newsletter heading, short description, email field, and button.
- Final legal row spans the width.
- Horizontal divider is extremely subtle.

### Newsletter form

- Input blends into the page rather than looking like a white form control.
- Use a bottom border or dark rectangular field.
- White submit button aligned right.
- On focus, use a brighter border; no neon glow.

---

## 13. Animation timeline from the recording

| Recording time | Visible behavior | Reconstruction method |
|---:|---|---|
| 0.0–1.5s | Hero centered with faint rings and stable sticky header | Initial staggered fade/translate entrance |
| 1.5–3.0s | Hero content begins moving upward | Scroll-linked translate, not an autonomous animation |
| 2.5–5.0s | Product/browser window rises from below, straightens, and enlarges | Sticky scroll track; y + scale + rotateX interpolation |
| 4.0–6.5s | Product media changes from dark map view to portrait/source image | Video playback or crossfade between media layers |
| 5.5–7.5s | Product media exits; enterprise heading enters | Overlapping section handoff with opposing y transforms |
| 7.0–9.5s | Feature pills and product card enter | Intersection reveal with small stagger |
| 9.0–11.5s | Editorial and intelligence content scroll into view | Image parallax + grouped copy reveal |
| 11.0–13.0s | Case-study card moves upward while next heading enters | Independent media/copy scroll speeds |
| 12.5–15.0s | Blog intro and three cards appear | Centered reveal followed by staggered cards |
| 14.5–16.0s | Footer/newsletter settles into view | Simple opacity/y reveal; no complex motion |

---

## 14. Recommended motion implementation

### Preferred stack

- Semantic HTML/React components.
- CSS custom properties for visual tokens.
- **GSAP ScrollTrigger** for the hero product-window choreography if exact scroll scrubbing is required.
- Native `IntersectionObserver` for ordinary section/card reveals.
- CSS transitions for hover, focus, tab, and button states.

Do not use a full animation library for every element. The page only needs one complex scroll sequence; the rest can remain native and lightweight.

### Scroll progress utility

```js
const clamp = (n, min = 0, max = 1) => Math.min(max, Math.max(min, n));

function sectionProgress(element) {
  const rect = element.getBoundingClientRect();
  const travel = window.innerHeight + rect.height;
  return clamp((window.innerHeight - rect.top) / travel);
}
```

Write progress into CSS variables inside one `requestAnimationFrame` loop:

```js
hero.style.setProperty('--hero-progress', progress);
```

Then calculate transforms in CSS where possible. This avoids repeatedly setting many inline properties.

### Reveal utility

```css
[data-reveal] {
  opacity: 0;
  transform: translateY(32px);
  transition:
    opacity 800ms cubic-bezier(.22,1,.36,1),
    transform 800ms cubic-bezier(.22,1,.36,1);
}

[data-reveal].is-visible {
  opacity: 1;
  transform: translateY(0);
}
```

---

## 15. Responsive behavior

### Tablet (768–1023px)

- Reduce hero H1 to 48–58px.
- Product-window width becomes approximately 88vw.
- Two-column feature sections may remain split, but gap is reduced.
- Blog grid becomes two columns; the third card wraps.
- Header hides one level of navigation.

### Mobile (<768px)

- Header becomes brand + two actions/menu.
- Hero H1 uses 42–48px with tighter line height.
- Ring field stays but is cropped outside the viewport.
- Remove `rotateX` from the product mockup; use only translate and scale.
- Stack every two-column section.
- Keep media above copy when the image is the narrative focus; otherwise copy first.
- Feature pills become a horizontally scrolling row.
- Blog cards become one column.
- Newsletter form stacks input and button.
- Disable heavy parallax on low-power or reduced-motion devices.

### Reduced motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    scroll-behavior: auto !important;
    transition-duration: .01ms !important;
  }
}
```

The content must remain fully visible when motion is disabled.

---

## 16. Media and asset preparation

Prepare the following original assets before implementation:

1. Vector brand mark in light and dark variants.
2. Product-interface demo video or two/three staged screenshots.
3. One portrait/source-image scene suitable for the oval mask.
4. Feature-card image.
5. Wide monochrome editorial image.
6. Vertical case-study image.
7. Three blog-card images.
8. Minimal line icons for navigation and feature pills.

Media rules:

- Export photographs as AVIF/WebP with JPEG fallback if needed.
- Use `srcset` for card images.
- Keep the hero demo video muted, inline, and poster-backed.
- Load media below the hero lazily.
- Reserve explicit aspect ratios to prevent layout shifts.

---

## 17. Performance requirements

- Target initial JavaScript under 150KB compressed before optional animation libraries.
- Use one scroll listener at most, scheduled through `requestAnimationFrame`.
- Animate only `transform`, `opacity`, and occasionally `filter`.
- Never animate layout properties such as `top`, `height`, or `margin` during scroll.
- Limit backdrop blur to the header and one or two overlays.
- Keep all product-video decoding out of the main thread.
- Target Largest Contentful Paint under 2.5 seconds on a normal broadband connection.
- Avoid autoplaying multiple videos simultaneously.

---

## 18. Accessibility requirements

- Maintain a logical heading hierarchy.
- Give the theme switch, search action, and menu explicit accessible names.
- Ensure all white-on-dark text meets WCAG AA contrast.
- Provide visible keyboard focus indicators.
- Pause or disable product-demo motion when the user requests reduced motion.
- Use actual buttons for tabs and controls, not clickable `div` elements.
- Feature tabs require `role="tablist"`, `role="tab"`, and `aria-selected`.
- Decorative rings and background media should be hidden from assistive technology.
- Videos require a textual alternative or concise description.

---

## 19. Suggested implementation order

1. Build the semantic page with no animation.
2. Add design tokens, fonts, container widths, and vertical rhythm.
3. Match the hero composition at 1910 × 970 first.
4. Build the sticky header and responsive menu.
5. Create the product-window mockup and media masks.
6. Implement the hero sticky scroll track.
7. Build enterprise intro, tabs, and feature card.
8. Build editorial, intelligence, blog, and footer sections.
9. Add ordinary reveal animations with `IntersectionObserver`.
10. Add hover/focus states.
11. Tune mobile/tablet layouts.
12. Add reduced-motion handling.
13. Run performance and accessibility checks.
14. Record a scroll-through at 1910 × 970 and compare it side-by-side with the supplied video.

---

## 20. Visual acceptance checklist

- [ ] Background reads as neutral black, not navy or charcoal blue.
- [ ] Sticky header remains compact and visually quiet.
- [ ] Hero copy is vertically centered at initial load.
- [ ] Concentric rings remain barely visible.
- [ ] Product window rises and straightens smoothly with scroll.
- [ ] Product media transition is masked cleanly with no layout jump.
- [ ] Each major section occupies approximately one viewport of narrative space.
- [ ] Feature tabs and cards use restrained borders and surfaces.
- [ ] Images are predominantly desaturated/darkened to preserve text hierarchy.
- [ ] Blog cards reveal with a subtle stagger.
- [ ] Footer contains ample negative space.
- [ ] Mobile layout does not retain desktop 3D transforms.
- [ ] Reduced-motion mode displays all content without relying on animation.
- [ ] No copied GeoSpy/Raven names, copy, or proprietary media appear in the finished implementation.

---

## 21. What should not be added

To preserve the visual character seen in the recording, avoid:

- Neon-green cybersecurity styling.
- Animated radar scanners or map grids unrelated to the marketing narrative.
- Glassmorphism on every card.
- Excessive rounded pills.
- Gradient text.
- Continuous floating animations.
- Large icon collections.
- Dense dashboards or visible technical pipelines.
- Multiple competing accent colors.
- Scroll hijacking that prevents normal keyboard/touch scrolling.

The correct result should feel like a cinematic editorial product page, not a dashboard and not a template-generated landing page.
