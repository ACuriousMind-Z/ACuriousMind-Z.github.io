---
layout: homepage
---

## About Me

{% comment %}
The biography below comes from the ORCID record via
cv/scripts/fetch_orcid_profile.py -> _data/orcid.yml, so editing it at
https://orcid.org/my-orcid updates this page on the next sync. The
hand-written text is the fallback whenever ORCID has no public biography,
and is also what to restore if you'd rather this page not track ORCID at
all: keep it and drop the surrounding if/else/endif tags.
{% endcomment %}
{% assign orcid_bio = site.data.orcid.biography | default: "" | strip %}
{% if orcid_bio != "" %}
{{ orcid_bio | markdownify }}
{% else %}
I am a postdoctoral fellow in the Department of Mechanical and Industrial
Engineering at the University of Toronto, working in the
[Microcellular Plastics Manufacturing Laboratory](https://mpml.mie.utoronto.ca/lab/)
with Prof. Chul B. Park.

My research develops multifunctional porous polymers in which structure is used as
the functional element. Using supercritical CO<sub>2</sub> foaming, multilayer
coextrusion, and laser-based micro/nanostructuring, I control cellular and layered
architecture across the length scales that govern solar scattering, mid-infrared
emission, heat transport, and mechanical response. Current work targets passive
daytime radiative cooling (PDRC), shape-memory foams that adapt their insulation to
ambient conditions, and solvent-free routes that scale to building envelope
applications.
{% endif %}

## Education

<ul style="margin:0 0 20px;">
  <li><strong>Ph.D., Mechanical Engineering</strong>, University of Toronto, 2025<br>
  <em>Advisor: Prof. Patrick C. Lee</em></li>
  <li><strong>M.Sc., Chemical Process Equipment</strong>, Zhejiang University, 2020<br>
  <em>Advisor: Prof. Zhongbin Xu</em></li>
  <li><strong>B.Sc., Control Engineering</strong>, Xi'an Jiaotong University, 2017</li>
</ul>

## Research Interests

- Multifunctional polymeric materials and advanced manufacturing
- Micro/nano-cellular and micro/nano-layered structures
- Supercritical CO<sub>2</sub> foaming and cellular structure control
- Passive daytime radiative cooling and adaptive thermal management
- Polymer/mineral composites for optical and thermal management
- Shape-memory and adaptive polymer foams
- Energy-efficient building envelopes
- Physics-constrained machine learning for polymer processing
- LLM-based automation in polymer processing research

## Technical Expertise

<ul style="margin:0 0 20px;">
  <li>Multilayer coextrusion of micro/nano-layered films, 17 to 500+ layers</li>
  <li>Supercritical CO<sub>2</sub> foaming, including confined foaming in layered structures</li>
  <li>In situ visualization of cell nucleation and growth under high-pressure CO<sub>2</sub></li>
  <li>Solar reflectance and mid-infrared emissivity measurement (UV-Vis-NIR, FTIR) for radiative cooling</li>
  <li>Thermal conductivity and steady-state insulation measurement</li>
  <li>In situ micro-tensile testing of thin films</li>
  <li>CO<sub>2</sub> and UV laser micro/nanostructuring, including laser-induced forward transfer</li>
  <li>Porous microcapillary film extrusion and gas-assisted microextrusion</li>
</ul>

## Collaboration

I am interested in collaborations on radiative cooling and adaptive thermal
management materials, confined foaming and layered polymer processing, and
scale-up of these structures toward building and textile applications. I am also
open to work applying machine learning to foaming and processing data. Email is
the best way to reach me.

{% include_relative _includes/publications.md %}

{% include_relative _includes/services.md %}
