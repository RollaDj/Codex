"""Render the four procedural décor plates with Blender (Cycles, CPU).

    pip install bpy==5.2.3          # Blender as a Python module (~400 MB, one time)
    python tools/render_decors.py [--only studio-noir] [--samples 48] [--preview --scale 0.25]

Free, reproducible plates (1215x2160, sharp — depth of field is applied later by
the compositor so each décor stays tunable). They can be swapped at any time
for a real photo or an AI-generated still: drop it as decors/<id>/plate.jpg.
"""
from __future__ import annotations

import argparse
import math
import random
import subprocess
import sys
from pathlib import Path

import bpy  # noqa: I001 — bpy must be imported before bmesh / mathutils
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parent.parent
RES = (1350, 2400)


# ----------------------------------------------------------------- scene helpers
def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(samples: int, exposure: float = 0.0) -> None:
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 8
    sc.render.resolution_x, sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.exposure = exposure
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 95


def world(color=(0.02, 0.02, 0.02), strength=1.0) -> None:
    w = bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*color, 1)
    bg.inputs["Strength"].default_value = strength


def camera(loc=(0, -1.3, 1.38), target=(0, 3, 1.45), lens=30.0) -> None:
    cam = bpy.data.cameras.new("Cam")
    cam.lens = lens
    cam.sensor_width = 36
    obj = bpy.data.objects.new("Cam", cam)
    bpy.context.collection.objects.link(obj)
    obj.location = loc
    d = Vector(target) - Vector(loc)
    obj.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = obj


def material(name, color, rough=0.5, metal=0.0, emission=None, strength=0.0,
             noise=None, bump=0.0, coat=0.0) -> bpy.types.Material:
    """Principled material; `noise`=(scale, amount) adds subtle colour variation."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*color, 1)
    p.inputs["Roughness"].default_value = rough
    p.inputs["Metallic"].default_value = metal
    if coat:
        p.inputs["Coat Weight"].default_value = coat
    if emission:
        p.inputs["Emission Color"].default_value = (*emission, 1)
        p.inputs["Emission Strength"].default_value = strength
    if noise or bump:
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = noise[0] if noise else 40
        tex.inputs["Detail"].default_value = 6
        if noise:
            ramp = nt.nodes.new("ShaderNodeValToRGB")
            amt = noise[1]
            ramp.color_ramp.elements[0].color = (*[c * (1 - amt) for c in color], 1)
            ramp.color_ramp.elements[1].color = (*[min(1, c * (1 + amt)) for c in color], 1)
            nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
            nt.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
        if bump:
            b = nt.nodes.new("ShaderNodeBump")
            b.inputs["Strength"].default_value = bump
            nt.links.new(tex.outputs["Fac"], b.inputs["Height"])
            nt.links.new(b.outputs["Normal"], p.inputs["Normal"])
    return m


def wood(name, base=(0.32, 0.18, 0.09), dark=(0.16, 0.08, 0.035), scale=6.0, planks=False,
         rough=0.45, grain="Z") -> bpy.types.Material:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Roughness"].default_value = rough
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1, 1, 1)
    nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.bands_direction = grain           # grain lines run perpendicular to this axis
    wave.inputs["Scale"].default_value = scale
    wave.inputs["Distortion"].default_value = 2.5
    wave.inputs["Detail"].default_value = 6
    wave.inputs["Detail Scale"].default_value = 1.5
    nt.links.new(mapping.outputs["Vector"], wave.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*dark, 1)
    ramp.color_ramp.elements[1].color = (*base, 1)
    if planks:
        brick = nt.nodes.new("ShaderNodeTexBrick")
        brick.inputs["Scale"].default_value = 1.0
        brick.inputs["Mortar Size"].default_value = 0.004
        brick.inputs["Brick Width"].default_value = 0.9
        brick.inputs["Row Height"].default_value = 0.09
        brick.inputs["Color1"].default_value = (*base, 1)
        brick.inputs["Color2"].default_value = (*[c * 0.8 for c in base], 1)
        brick.inputs["Mortar"].default_value = (*[c * 0.4 for c in dark], 1)
        nt.links.new(coord.outputs["Object"], brick.inputs["Vector"])
        mix = nt.nodes.new("ShaderNodeValToRGB")
        nt.links.new(wave.outputs["Fac"], mix.inputs["Fac"])
        mix.color_ramp.elements[0].color = (0.75, 0.75, 0.75, 1)
        mix.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1)
        mult = nt.nodes.new("ShaderNodeVectorMath")
        mult.operation = "MULTIPLY"
        nt.links.new(brick.outputs["Color"], mult.inputs[0])
        nt.links.new(mix.outputs["Color"], mult.inputs[1])
        nt.links.new(mult.outputs["Vector"], p.inputs["Base Color"])
    else:
        nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
        nt.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
    return m


def marble(name, base=(0.85, 0.84, 0.82), vein=(0.35, 0.35, 0.36), rough=0.12,
           scale=2.0) -> bpy.types.Material:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    p = nt.nodes["Principled BSDF"]
    p.inputs["Roughness"].default_value = rough
    p.inputs["Coat Weight"].default_value = 0.3
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = scale
    wave.inputs["Distortion"].default_value = 18
    wave.inputs["Detail"].default_value = 8
    wave.inputs["Detail Scale"].default_value = 2.5
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*vein, 1)
    ramp.color_ramp.elements[1].position = 0.06
    ramp.color_ramp.elements[1].color = (*base, 1)
    nt.links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], p.inputs["Base Color"])
    return m


def box(name, size, loc, mat, rot=(0, 0, 0), bevel=0.0) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = o.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    o.data.materials.append(mat)
    return o


def cylinder(name, r, depth, loc, mat, rot=(0, 0, 0), verts=48) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    o.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return o


def sphere(name, r, loc, mat, scale=(1, 1, 1)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_uv_sphere_add(segments=48, ring_count=24, radius=r, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    o.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    return o


def area(name, loc, target, size, energy, color=(1, 1, 1), size_y=None) -> None:
    lt = bpy.data.lights.new(name, "AREA")
    lt.energy = energy
    lt.color = color
    lt.shape = "RECTANGLE"
    lt.size = size
    lt.size_y = size_y or size
    o = bpy.data.objects.new(name, lt)
    bpy.context.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def point(name, loc, energy, color=(1, 0.8, 0.6), radius=0.05) -> None:
    lt = bpy.data.lights.new(name, "POINT")
    lt.energy = energy
    lt.color = color
    lt.shadow_soft_size = radius
    o = bpy.data.objects.new(name, lt)
    bpy.context.collection.objects.link(o)
    o.location = loc


def frame_moulding(x0, z0, w, h, y, mat, t=0.035, d=0.025) -> None:
    """Rectangular raised moulding (boiserie) on a wall facing -Y at depth y."""
    box("m_t", (w, d, t), (x0 + w / 2, y - d / 2, z0 + h), mat, bevel=0.008)
    box("m_b", (w, d, t), (x0 + w / 2, y - d / 2, z0), mat, bevel=0.008)
    box("m_l", (t, d, h), (x0, y - d / 2, z0 + h / 2), mat, bevel=0.008)
    box("m_r", (t, d, h), (x0 + w, y - d / 2, z0 + h / 2), mat, bevel=0.008)


# ----------------------------------------------------------------- the four décors
def spot(name, loc, target, energy, size_deg=45, blend=1.0, color=(1, 1, 1), radius=0.3) -> None:
    lt = bpy.data.lights.new(name, "SPOT")
    lt.energy = energy
    lt.color = color
    lt.spot_size = math.radians(size_deg)
    lt.spot_blend = blend
    lt.shadow_soft_size = radius
    o = bpy.data.objects.new(name, lt)
    bpy.context.collection.objects.link(o)
    o.location = loc
    o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def studio_noir() -> None:
    world((0.0, 0.0, 0.0), 0.0)
    camera(lens=32)
    paper = material("paper", (0.018, 0.018, 0.019), rough=0.92, noise=(3.0, 0.3), bump=0.02)
    # Seamless cyclorama: floor → cove → wall.
    bm = bmesh.new()
    prof = [(y, 0.0) for y in (-4, 1.6)]
    r, cy, cz = 1.2, 1.6, 1.2
    prof += [(cy + r * math.sin(a), cz - r * math.cos(a)) for a in [i * math.pi / 2 / 12 for i in range(1, 13)]]
    prof += [(cy + r, 6.0)]
    rows = []
    for x in (-6.0, 6.0):
        rows.append([bm.verts.new((x, y, z)) for y, z in prof])
    for i in range(len(prof) - 1):
        bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i]))
    me = bpy.data.meshes.new("cyc")
    bm.to_mesh(me)
    obj = bpy.data.objects.new("cyc", me)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(paper)
    for poly in me.polygons:
        poly.use_smooth = True
    # Soft oval pool of warm light on the backdrop, centred behind the head; the rest
    # of the frame falls off to near-black.
    spot("pool", (0.0, -0.6, 1.9), (0.0, 2.8, 2.05), 1500, size_deg=46, blend=1.0,
         color=(1.0, 0.9, 0.78), radius=0.6)
    area("ambient", (0.0, -1.0, 3.0), (0.0, 2.8, 1.5), 4.0, 60, (0.9, 0.92, 1.0))


def haussmannien() -> None:
    world((0.75, 0.82, 0.95), 0.08)
    camera(lens=30)
    wall_c = (0.66, 0.62, 0.56)
    paint = material("paint", wall_c, rough=0.75, noise=(25, 0.03))
    trim = material("trim", (0.70, 0.67, 0.61), rough=0.5)
    floor = wood("parquet", base=(0.24, 0.13, 0.06), dark=(0.10, 0.05, 0.025), planks=True, rough=0.25,
                 grain="Y", scale=14)
    bw = 3.1  # back wall y
    box("floor", (8, 8, 0.05), (0, 1, -0.025), floor)
    box("ceiling", (8, 8, 0.05), (0, 1, 3.6), paint)
    box("back", (8, 0.1, 3.6), (0, bw + 0.05, 1.8), paint)
    box("left", (0.1, 8, 3.6), (-2.7, 1, 1.8), paint)
    box("right", (0.1, 8, 3.6), (2.7, 1, 1.8), paint)
    # Cornice + baseboard + dado rail.
    box("cornice", (8, 0.14, 0.16), (0, bw - 0.07, 3.5), trim, bevel=0.02)
    box("cornice2", (8, 0.06, 0.05), (0, bw - 0.03, 3.36), trim, bevel=0.01)
    box("plinth", (8, 0.03, 0.2), (0, bw - 0.015, 0.1), trim, bevel=0.005)
    box("dado", (8, 0.04, 0.05), (0, bw - 0.02, 0.95), trim, bevel=0.01)
    # Boiserie panels between the window and the fireplace (and beyond the frame edges).
    for x0, w in ((-0.42, 0.62), (-2.6, 0.9), (1.85, 0.8)):
        frame_moulding(x0, 1.12, w, 2.0, bw, trim)
        frame_moulding(x0, 0.3, w, 0.5, bw, trim)
    # Tall French window in the back wall (left of frame) with daylight + sheer curtains.
    glass_out = material("sky", (1, 1, 1), emission=(0.93, 0.95, 1.0), strength=2.2)
    wx, ww = -1.05, 0.9
    box("window_light", (ww, 0.02, 2.75), (wx, bw - 0.005, 1.6), glass_out)
    for z in (0.23, 1.15, 2.05, 2.95):
        box("mullion_h", (ww + 0.08, 0.06, 0.05), (wx, bw - 0.03, z), trim)
    for x in (wx - ww / 2, wx, wx + ww / 2):
        box("mullion_v", (0.05, 0.06, 2.8), (x, bw - 0.03, 1.6), trim)
    voile = bpy.data.materials.new("voile")
    voile.use_nodes = True
    pv = voile.node_tree.nodes["Principled BSDF"]
    pv.inputs["Base Color"].default_value = (0.95, 0.93, 0.88, 1)
    pv.inputs["Roughness"].default_value = 0.8
    pv.inputs["Transmission Weight"].default_value = 0.7
    pv.inputs["Alpha"].default_value = 0.55
    for x in (wx - ww / 2 - 0.12, wx + ww / 2 + 0.12):
        o = box("curtain", (0.35, 0.03, 3.0), (x, bw - 0.12, 1.65), voile)
        wave = o.modifiers.new("wave", "WAVE")
        wave.use_y, wave.use_x = True, False
        wave.height, wave.width = 0.05, 0.09
    area("sun", (wx, bw - 0.3, 1.8), (0.4, 0.0, 0.8), 1.0, 260, (1.0, 0.96, 0.9), size_y=2.6)
    box("front", (8, 0.1, 3.6), (0, -2.6, 1.8), paint)
    # Marble fireplace + gilded mirror (right of frame, visible beside the subject).
    stone = marble("carrara", base=(0.80, 0.79, 0.77), vein=(0.45, 0.45, 0.46), scale=3.0)
    gold = material("gold", (0.85, 0.65, 0.32), rough=0.25, metal=1.0)
    mirror = material("mirror", (0.95, 0.95, 0.95), rough=0.02, metal=1.0)
    fx = 1.05
    box("fp_top", (1.5, 0.35, 0.07), (fx, bw - 0.18, 1.08), stone, bevel=0.01)
    box("fp_l", (0.2, 0.25, 1.05), (fx - 0.6, bw - 0.13, 0.52), stone, bevel=0.01)
    box("fp_r", (0.2, 0.25, 1.05), (fx + 0.6, bw - 0.13, 0.52), stone, bevel=0.01)
    box("fp_in", (1.0, 0.05, 0.8), (fx, bw - 0.03, 0.4), material("soot", (0.02, 0.02, 0.02), 0.9))
    box("mir", (1.2, 0.02, 1.6), (fx, bw - 0.03, 2.05), mirror)
    box("mf_t", (1.32, 0.05, 0.07), (fx, bw - 0.04, 2.88), gold, bevel=0.015)
    box("mf_b", (1.32, 0.05, 0.07), (fx, bw - 0.04, 1.22), gold, bevel=0.015)
    box("mf_l", (0.07, 0.05, 1.7), (fx - 0.63, bw - 0.04, 2.05), gold, bevel=0.015)
    box("mf_r", (0.07, 0.05, 1.7), (fx + 0.63, bw - 0.04, 2.05), gold, bevel=0.015)
    # A few objects on the mantel + a plant in the left corner.
    cylinder("vase", 0.08, 0.32, (fx - 0.35, bw - 0.2, 1.28), material("ceramic", (0.9, 0.88, 0.84), 0.3))
    cylinder("candle", 0.03, 0.25, (fx + 0.4, bw - 0.2, 1.24), gold)
    pot = material("pot", (0.15, 0.14, 0.13), 0.6)
    leaf = material("leaf", (0.06, 0.16, 0.05), 0.5)
    cylinder("pot", 0.2, 0.42, (-1.75, 2.45, 0.21), pot)
    random.seed(3)
    for _ in range(26):
        sphere("leaf", 0.15, (-1.75 + random.uniform(-0.3, 0.3), 2.45 + random.uniform(-0.3, 0.2),
                              random.uniform(0.8, 2.0)), leaf, scale=(1.0, 0.22, 0.65))
    # Brass chandelier with candle bulbs above the head (becomes warm bokeh once blurred).
    warm = material("bulb", (1, 1, 1), emission=(1.0, 0.78, 0.5), strength=20)
    ring_c = (0.0, 1.9, 2.95)
    bpy.ops.mesh.primitive_torus_add(major_radius=0.32, minor_radius=0.012, location=ring_c)
    bpy.context.active_object.data.materials.append(gold)
    cylinder("chain", 0.008, 0.6, (ring_c[0], ring_c[1], 3.3), gold)
    for i in range(8):
        a = i / 8 * 2 * math.pi
        x, y = ring_c[0] + 0.32 * math.cos(a), ring_c[1] + 0.32 * math.sin(a)
        cylinder("cand", 0.012, 0.08, (x, y, 3.0), trim)
        sphere("bulb", 0.022, (x, y, 3.06), warm, scale=(1, 1, 1.5))
    point("chand", (ring_c[0], ring_c[1], 2.9), 90, (1.0, 0.8, 0.55), 0.3)


def interieur_design() -> None:
    world((0.1, 0.09, 0.08), 0.15)
    camera(lens=30)
    lime = material("limewash", (0.40, 0.35, 0.29), rough=0.88, noise=(3.0, 0.16), bump=0.05)
    oak = wood("oak", base=(0.30, 0.20, 0.12), dark=(0.18, 0.11, 0.06), planks=True, rough=0.35,
               grain="Y", scale=14)
    walnut = wood("walnut", base=(0.20, 0.11, 0.06), dark=(0.11, 0.055, 0.03), scale=14.0, rough=0.35)
    bw = 2.9
    box("floor", (9, 9, 0.05), (0, 1, -0.025), oak)
    box("back", (9, 0.1, 3.2), (0, bw + 0.05, 1.6), lime)
    box("ceiling", (9, 9, 0.05), (0, 1, 3.2), material("ceil", (0.7, 0.68, 0.65), 0.9))
    box("left", (0.1, 9, 3.2), (-2.8, 1, 1.6), lime)
    box("right", (0.1, 9, 3.2), (2.8, 1, 1.6), lime)
    # Recessed cove with a warm LED line across the top of the wall.
    led = material("led", (1, 1, 1), emission=(1.0, 0.72, 0.42), strength=10)
    box("cove", (9, 0.12, 0.08), (0, bw - 0.06, 2.95), material("cove", (0.4, 0.36, 0.31), 0.8))
    box("led", (9, 0.02, 0.012), (0, bw - 0.1, 2.905), led)
    area("wallwash", (0, bw - 0.15, 2.88), (0, bw, 1.2), 3.0, 60, (1.0, 0.75, 0.48), size_y=0.05)
    # Low walnut sideboard across the frame + objects on both sides of the subject.
    box("sideboard", (3.0, 0.45, 0.62), (0.0, bw - 0.25, 0.36), walnut, bevel=0.01)
    box("legs", (2.9, 0.4, 0.05), (0.0, bw - 0.25, 0.025), material("blk", (0.02, 0.02, 0.02), 0.4, 0.8))
    black_c = material("black_ceramic", (0.03, 0.03, 0.03), 0.35)
    sand_c = material("sand_ceramic", (0.62, 0.54, 0.44), 0.6, noise=(30, 0.06))
    sphere("vase1", 0.16, (-1.15, bw - 0.25, 0.84), black_c, scale=(1, 1, 1.4))
    cylinder("vase2", 0.08, 0.42, (-0.85, bw - 0.25, 0.88), sand_c)
    for i, c in enumerate([(0.62, 0.55, 0.45), (0.22, 0.27, 0.25), (0.75, 0.72, 0.66)]):
        box(f"book{i}", (0.32, 0.24, 0.045), (1.05, bw - 0.25, 0.695 + i * 0.047),
            material(f"bk{i}", c, 0.7), rot=(0, 0, 0.08 * i))
    sphere("obj", 0.07, (1.05, bw - 0.25, 0.86), material("brass_o", (0.8, 0.6, 0.35), 0.25, 1.0))
    # Abstract artwork, upper right.
    art = bpy.data.materials.new("art")
    art.use_nodes = True
    nt = art.node_tree
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 1.4
    tex.inputs["Distortion"].default_value = 1.5
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.38, (0.55, 0.30, 0.18, 1)
    els[1].position, els[1].color = 0.6, (0.80, 0.74, 0.64, 1)
    e = els.new(0.5)
    e.color = (0.22, 0.18, 0.15, 1)
    ramp.color_ramp.interpolation = "CONSTANT"
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
    box("canvas", (0.85, 0.04, 1.1), (0.95, bw - 0.03, 1.85), art)
    box("canvas_frame", (0.89, 0.03, 1.14), (0.95, bw - 0.01, 1.85), material("oakframe", (0.35, 0.25, 0.16), 0.5))
    # Opal globe pendants, upper left (warm bokeh once blurred).
    opal = material("opal", (1, 1, 1), 0.3, emission=(1.0, 0.82, 0.6), strength=8)
    cord = material("cord", (0.02, 0.02, 0.02), 0.5)
    for i, (x, y, z) in enumerate([(-0.95, 2.3, 2.25), (-1.25, 2.1, 2.5), (-0.7, 2.0, 2.6)]):
        sphere(f"globe{i}", 0.12, (x, y, z), opal)
        cylinder(f"cord{i}", 0.004, 3.2 - z, (x, y, z + (3.2 - z) / 2), cord)
        point(f"g{i}", (x, y, z), 25, (1.0, 0.82, 0.6), 0.12)
    # Arc floor lamp at the right edge.
    steel = material("steel", (0.6, 0.6, 0.6), 0.25, 1.0)
    cylinder("lamp_base", 0.16, 0.05, (1.45, 2.2, 0.025), material("marble_b", (0.8, 0.79, 0.77), 0.2))
    cylinder("lamp_pole", 0.014, 1.9, (1.45, 2.2, 0.95), steel)
    shade = material("shade", (0.9, 0.88, 0.82), 0.6, emission=(1.0, 0.75, 0.45), strength=5)
    sphere("lamp_shade", 0.2, (1.2, 2.2, 1.95), shade, scale=(1, 1, 0.55))
    cylinder("lamp_arm", 0.012, 0.35, (1.33, 2.2, 1.98), steel, rot=(0, math.radians(80), 0))
    point("lamp", (1.2, 2.2, 1.8), 90, (1.0, 0.75, 0.45), 0.15)
    # Plant, lower left.
    leaf = material("leaf2", (0.05, 0.12, 0.04), 0.45)
    cylinder("pot2", 0.22, 0.5, (-1.45, 2.25, 0.25), material("pot2", (0.5, 0.46, 0.42), 0.8, noise=(20, 0.08)))
    random.seed(7)
    for _ in range(70):
        o = sphere("lf", 0.11, (-1.45 + random.uniform(-0.42, 0.42), 2.25 + random.uniform(-0.3, 0.25),
                                random.uniform(0.75, 2.05)), leaf, scale=(1.0, 0.12, 0.45))
        o.rotation_euler = (random.uniform(-0.6, 0.6), random.uniform(-0.8, 0.8), random.uniform(0, math.pi))
    area("window", (2.7, 1.0, 1.7), (0.0, 2.6, 1.2), 2.0, 140, (1.0, 0.97, 0.92), size_y=2.4)


def hotel_luxe() -> None:
    world((0.01, 0.008, 0.006), 0.2)
    camera(lens=32, target=(0, 10, 1.5))
    walnut = wood("walnut_h", base=(0.20, 0.11, 0.06), dark=(0.08, 0.04, 0.02), scale=6.0, rough=0.3,
                  grain="Y")
    nero = marble("nero", base=(0.02, 0.02, 0.022), vein=(0.12, 0.115, 0.11), rough=0.08, scale=7.0)
    brass = material("brass", (0.82, 0.62, 0.36), 0.22, 1.0)
    warm = material("warm_glow", (1, 1, 1), emission=(1.0, 0.7, 0.4), strength=18)
    ceil = material("ceil_h", (0.05, 0.04, 0.035), 0.8)
    L = 14
    box("floor", (7, L + 4, 0.05), (0, L / 2, -0.025), nero)
    box("ceiling", (7, L + 4, 0.05), (0, L / 2, 3.4), ceil)
    box("end", (7, 0.1, 3.4), (0, L + 0.5, 1.7), walnut)
    for side in (-1, 1):
        x = side * 2.4
        box(f"wall{side}", (0.1, L + 4, 3.4), (x + side * 0.08, L / 2, 1.7), walnut)
        # Fluted walnut slats along both walls.
        for i in range(int(L / 0.07)):
            y = 0.5 + i * 0.07
            cylinder(f"flute{side}_{i}", 0.026, 3.0, (x, y, 1.6), walnut, verts=12)
        # Brass sconces every 2.4 m with warm glow + light.
        for k in range(6):
            y = 1.4 + k * 2.3
            cylinder(f"sc{side}{k}", 0.05, 0.45, (x - side * 0.1, y, 1.9), warm)
            box(f"scb{side}{k}", (0.03, 0.14, 0.6), (x - side * 0.05, y, 1.9), brass)
            point(f"scl{side}{k}", (x - side * 0.25, y, 1.9), 45, (1.0, 0.72, 0.42), 0.08)
    # Recessed ceiling downlights.
    for k in range(10):
        y = 1.0 + k * 1.4
        cylinder(f"dl{k}", 0.06, 0.01, (0, y, 3.37), warm)
        spot(f"dll{k}", (0, y, 3.3), (0, y, 0), 120, size_deg=70, blend=0.6, color=(1.0, 0.78, 0.5), radius=0.05)
    # Chandelier in the lounge at the end + brass console with flowers.
    for i in range(40):
        a = i / 40 * 2 * math.pi * 3
        r = 0.25 + 0.012 * i
        sphere(f"crys{i}", 0.03, (r * math.cos(a), L - 1.5 + r * math.sin(a), 2.9 - i * 0.012), warm)
    point("chand_h", (0, L - 1.5, 2.6), 300, (1.0, 0.75, 0.45), 0.4)
    box("console", (1.8, 0.45, 0.85), (0, L - 0.3, 0.425), brass, bevel=0.01)
    vase = material("vase_h", (0.9, 0.88, 0.85), 0.15)
    cylinder("vase_h", 0.12, 0.45, (0, L - 0.3, 1.07), vase)
    blossom = material("blossom", (0.85, 0.82, 0.78), 0.6)
    random.seed(11)
    for _ in range(30):
        sphere("fl", 0.06, (random.uniform(-0.4, 0.4), L - 0.3 + random.uniform(-0.15, 0.15),
                            random.uniform(1.3, 1.85)), blossom)
    area("fill", (0, -0.5, 2.8), (0, 4, 1.2), 2.0, 60, (1.0, 0.85, 0.7))



# ----------------------------------------------------------------- brand wall (logos in frames)
LOGO_DIR = ROOT / "decors" / "mur-noir-logos" / "logos"
PAPER, INK = (234, 229, 219), (16, 15, 14)


def _font(family: str, size: int):
    from PIL import ImageFont
    path = subprocess.check_output(["fc-match", "-f", "%{file}", family]).decode()
    return ImageFont.truetype(path, size)


def _load_logo(path: Path):
    """PNG/JPG/WEBP directly; SVG rasterised with FFmpeg (librsvg)."""
    from PIL import Image
    if path.suffix.lower() == ".svg":
        png = path.with_suffix(".raster.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-width", "2000", "-i", str(path),
                        "-frames:v", "1", str(png)], check=True)
        path = png
    return Image.open(path).convert("RGBA")


def make_print(out: Path, stem: str, lines: list[tuple[str, str, int, int]]) -> Path:
    """Paper + logo for one frame. Uses decors/mur-noir-logos/logos/<stem>.(svg|png|jpg|webp)
    when present (official logo); otherwise a provisional typographic wordmark."""
    from PIL import Image, ImageDraw
    W, H = 1000, 1300
    img = Image.new("RGB", (W, H), PAPER)
    logo = next((LOGO_DIR / f"{stem}{ext}" for ext in (".svg", ".png", ".webp", ".jpg", ".jpeg")
                 if (LOGO_DIR / f"{stem}{ext}").exists()), None)
    if logo is not None:
        import numpy as np
        lg = _load_logo(logo)
        arr = np.asarray(lg)
        if arr[..., 3].min() == 255:
            # Opaque artwork (e.g. "ivory on black"): keep the official colourway. The
            # print takes the artwork's background colour and the logo is cropped to its ink.
            rgb = arr[..., :3].astype(int)
            border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
            bg = tuple(int(v) for v in np.median(border, 0))
            ys, xs = np.where(np.abs(rgb - bg).max(-1) > 40)
            if len(xs):
                lg = lg.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
            img = Image.new("RGB", (W, H), bg)
            fit_w, fit_h = 0.74, 0.50
        else:
            fit_w, fit_h = 0.62, 0.42
        k = min(W * fit_w / lg.width, H * fit_h / lg.height)
        lg = lg.resize((int(lg.width * k), int(lg.height * k)), Image.LANCZOS)
        img.paste(lg, ((W - lg.width) // 2, int((H - lg.height) * 0.47)), lg)
    else:
        d = ImageDraw.Draw(img)
        blocks = []
        for text, family, size, track in lines:
            f = _font(family, size)
            widths = [d.textlength(c, font=f) for c in text]
            blocks.append((text, f, widths, sum(widths) + track * (len(text) - 1), size, track))
        total_h = sum(b[4] for b in blocks) + 40 * (len(blocks) - 1)
        y = (H - total_h) / 2
        for text, f, widths, tw, size, track in blocks:
            x = (W - tw) / 2
            for c, cw in zip(text, widths):
                d.text((x, y), c, font=f, fill=INK)
                x += cw + track
            y += size + 40
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    return out


def image_material(name: str, path: Path, rough: float = 0.85) -> bpy.types.Material:
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(path))
    p = nt.nodes["Principled BSDF"]
    nt.links.new(tex.outputs["Color"], p.inputs["Base Color"])
    p.inputs["Roughness"].default_value = rough
    return m


def framed_print(x: float, zc: float, w: float, h: float, wall_y: float, print_path: Path,
                 frame_mat, light: bool = True) -> None:
    bpy.ops.mesh.primitive_plane_add(size=1, location=(x, wall_y - 0.012, zc), rotation=(math.pi / 2, 0, 0))
    o = bpy.context.active_object
    o.scale = (w, h, 1)
    o.data.materials.append(image_material(f"print_{x}", print_path))
    frame_moulding(x - w / 2, zc - h / 2, w, h, wall_y, frame_mat, t=0.022, d=0.034)
    box("backing", (w, 0.01, h), (x, wall_y - 0.006, zc), material("back", (0.02, 0.02, 0.02), 0.9))
    if light:   # brass picture light above the frame
        zl = zc + h / 2 + 0.11
        cylinder("pl_bar", 0.014, w * 0.62, (x, wall_y - 0.13, zl), frame_mat, rot=(0, math.pi / 2, 0))
        cylinder("pl_arm", 0.006, 0.13, (x, wall_y - 0.065, zl + 0.01), frame_mat, rot=(math.pi / 2, 0, 0))
        area(f"pl_{x}", (x, wall_y - 0.15, zl - 0.02), (x, wall_y, zc - h * 0.1), 0.02, 9,
             (1.0, 0.78, 0.55), size_y=w * 0.55)


def _logo_prints() -> tuple[Path, Path]:
    tmp = ROOT / "decors" / "mur-noir-logos" / "prints"
    left = make_print(tmp / "maison-belier.jpg", "maison-belier",
                      [("MAISON", "Caladea", 132, 34), ("BÉLIER", "Caladea", 132, 34)])
    right = make_print(tmp / "rolla.jpg", "rolla", [("ROLLA", "Inter", 170, 58)])
    return left, right


def mur_noir_logos() -> None:
    """Matte black wall, two brass frames with the Maison Bélier and ROLLA logos,
    brass picture lights. Camera matches a seated, low-angle iPhone selfie."""
    world((0.0, 0.0, 0.0), 0.0)
    camera(loc=(0, -1.0, 1.05), target=(0, 1.6, 1.5), lens=26)
    wall_y = 1.6
    black = material("black_paint", (0.022, 0.022, 0.024), rough=0.82, noise=(6.0, 0.18), bump=0.04)
    brass = material("brass_f", (0.80, 0.60, 0.34), 0.28, 1.0)
    box("wall", (6, 0.1, 4), (0, wall_y + 0.05, 2.0), black)
    box("floor", (6, 6, 0.05), (0, 0, -0.025), wood("floor_o", base=(0.16, 0.10, 0.06),
                                                     dark=(0.07, 0.04, 0.02), planks=True, grain="Y", scale=14))
    box("skirting", (6, 0.02, 0.12), (0, wall_y - 0.01, 0.06), black)
    left, right = _logo_prints()
    w, h, zc = 0.42, 0.55, 2.0
    framed_print(-0.58, zc, w, h, wall_y, left, brass)
    framed_print(0.58, zc, w, h, wall_y, right, brass)
    # Soft fill so the black wall reads as a painted surface, not a void.
    area("fill", (0, -0.6, 2.4), (0, wall_y, 1.6), 3.0, 35, (0.95, 0.95, 1.0))
    spot("glow", (0, -0.4, 1.3), (0, wall_y, 1.75), 220, size_deg=55, blend=1.0,
         color=(1.0, 0.86, 0.7), radius=0.5)


def mur_clair_logos() -> None:
    """Same composition on a warm off-white limewash wall in soft daylight, with thin
    black frames: matches footage shot in a bright room (no relight of the subject)."""
    world((0.85, 0.88, 0.95), 0.06)
    camera(loc=(0, -1.0, 1.05), target=(0, 1.6, 1.5), lens=26)
    wall_y = 1.6
    lime = material("limewash_c", (0.72, 0.69, 0.64), rough=0.9, noise=(2.5, 0.07), bump=0.06)
    black_f = material("black_frame", (0.015, 0.015, 0.016), 0.35)
    box("wall", (6, 0.1, 4), (0, wall_y + 0.05, 2.0), lime)
    box("floor", (6, 6, 0.05), (0, 0, -0.025), wood("floor_c", base=(0.42, 0.30, 0.19),
                                                     dark=(0.28, 0.19, 0.11), planks=True, grain="Y", scale=14))
    box("skirting", (6, 0.02, 0.12), (0, wall_y - 0.01, 0.06), material("sk", (0.78, 0.76, 0.72), 0.6))
    left, right = _logo_prints()
    w, h, zc = 0.42, 0.55, 2.0
    framed_print(-0.58, zc, w, h, wall_y, left, black_f, light=False)
    framed_print(0.58, zc, w, h, wall_y, right, black_f, light=False)
    # Big soft window to camera right (like the original room) + bounce from the left.
    area("window", (2.2, 0.4, 2.0), (0.0, wall_y, 1.5), 2.4, 260, (0.98, 0.99, 1.0), size_y=2.8)
    area("bounce", (-1.8, -0.3, 1.6), (0.0, wall_y, 1.5), 2.0, 30, (1.0, 0.97, 0.92))


SCENES = {
    "studio-noir": (studio_noir, -0.3),
    "haussmannien": (haussmannien, -0.7),
    "interieur-design": (interieur_design, -0.35),
    "hotel-luxe": (hotel_luxe, 0.0),
    "mur-noir-logos": (mur_noir_logos, 0.0),
    "mur-clair-logos": (mur_clair_logos, 0.0),
}


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--scale", type=float, default=0.9,
                    help="résolution relative à 1350x2400 (0.9 = 1215x2160, la taille utile)")
    ap.add_argument("--preview", action="store_true", help="écrit preview.jpg au lieu de plate.jpg")
    a = ap.parse_args(argv)
    for key, (build, expo) in SCENES.items():
        if a.only and key != a.only:
            continue
        reset()
        setup_render(a.samples, expo)
        bpy.context.scene.render.resolution_percentage = int(100 * a.scale)
        build()
        out = ROOT / "decors" / key / ("preview.jpg" if a.preview else "plate.jpg")
        out.parent.mkdir(parents=True, exist_ok=True)
        bpy.context.scene.render.filepath = str(out)
        print(f"Rendu {key} → {out}", flush=True)
        bpy.ops.render.render(write_still=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
