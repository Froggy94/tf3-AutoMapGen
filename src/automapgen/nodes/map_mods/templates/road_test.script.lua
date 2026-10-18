-- Made by automapgen (road test mod) - builds test roads next to every street the player builds, to find out
-- what the game does with given heights, bridge and tunnel edges, junctions to existing nodes, tight curves and the
-- steep streets and retaining walls of the mod DHE_Walls_Tracks_Streets (it must be active for T5).
-- Log: local/crash_dump/stdout.txt, lines "[dhe_road_test]". Everything kept between calls is in the script state:
-- the game does not keep the script's local variables from one call to the next (test 2026-10-14).
local DATA = {
	template = "@TEMPLATE@",
	wallsModId = "@WALLS_MOD_ID@",
	steepTemplate = "@STEEP_TEMPLATE@",
	lowerWalls = { @LOWER_WALLS@ },
	upperWalls = { @UPPER_WALLS@ },
	wallFrom = @WALL_FROM@,
	bridgeTypes = { @BRIDGE_TYPES@ },
	tunnelTypes = { @TUNNEL_TYPES@ },
	bridgeAbove = @BRIDGE_ABOVE@,
	tunnelBelow = @TUNNEL_BELOW@,
	lineLength = @LINE_LENGTH@,
	edgeLength = @EDGE_LENGTH@,
	lineOffset = @LINE_OFFSET@,
	branchLength = @BRANCH_LENGTH@,
	radii = { @RADII@ },
	legLength = @LEG_LENGTH@,
}

local SCAN_CALLS = 10       -- look for new streets of the player every this many calls
local JUNCTION_TRIES = 20   -- calls to wait for the nodes of T2 before T3 gives up
local tpl, steepTpl, bridgeIndex, tunnelIndex, lowerWall, upperWall = nil, nil, nil, nil, nil, nil
local quiet = false         -- resolve without logging (the names were logged once)

local function log(msg)
	print("[dhe_road_test] " .. msg)
end

-- vectors: plain tables {x, y, z}
local function add(a, b) return { a[1] + b[1], a[2] + b[2], (a[3] or 0) + (b[3] or 0) } end
local function mul(a, f) return { a[1] * f, a[2] * f, (a[3] or 0) * f } end
local function sub(a, b) return { a[1] - b[1], a[2] - b[2], (a[3] or 0) - (b[3] or 0) } end
local function len(a) return math.sqrt(a[1] * a[1] + a[2] * a[2] + (a[3] or 0) * (a[3] or 0)) end
local function vec3(a) return api.type.Vec3f.new(a[1], a[2], a[3]) end
local function ground(p) return api.engine.terrain.getHeightAt(api.type.Vec2f.new(p[1], p[2])) end
local function onGround(p) return { p[1], p[2], ground(p) } end

local function streetTemplate(name)
	for _, prefix in ipairs({ "::/infrastructure/", "/infrastructure/", DATA.wallsModId .. "::/infrastructure/" }) do
		local id = api.res.streetTemplateRep.find(prefix .. name)
		if id and id >= 0 then
			local t = api.res.streetTemplateRep.get(id)
			return { name = prefix .. name, style = t.streetStyle, lanes = t.laneConfigs }
		end
	end
	return nil
end

-- the first name the repository knows (all tried names are logged)
local function firstKnown(rep, names)
	local found = nil
	for _, name in ipairs(names) do
		local ok, id = pcall(function() return api.res[rep].find(name) end)
		if not quiet then log(string.format("%s %s -> %s", rep, name, tostring(ok and id or "error"))) end
		if ok and id and id >= 0 and not found then found = id end
	end
	return found
end

local function resolve()
	tpl = streetTemplate(DATA.template)
	steepTpl = streetTemplate(DATA.steepTemplate)
	if not quiet then
		log(string.format("steep street %s: %s", DATA.steepTemplate,
			steepTpl and steepTpl.name or "NOT FOUND (mod active?)"))
	end
	lowerWall = firstKnown("edgeDecorationRep", DATA.lowerWalls)
	upperWall = firstKnown("edgeDecorationRep", DATA.upperWalls)
	for _, rep in ipairs(quiet and {} or { "bridgeTypeRep", "tunnelTypeRep", "edgeDecorationRep" }) do
		local ok, all = pcall(function() return api.res[rep].getAll() end)
		if ok and all then
			local names = {}
			for k, v in pairs(all) do names[#names + 1] = tostring(k) .. "=" .. tostring(v) end
			log(rep .. ": " .. table.concat(names, ", "))
		else
			log(rep .. ".getAll not available: " .. tostring(all))
		end
	end
	bridgeIndex = firstKnown("bridgeTypeRep", DATA.bridgeTypes)
	tunnelIndex = firstKnown("tunnelTypeRep", DATA.tunnelTypes)
	if not quiet then
		log(string.format("street %s, bridge index %s, tunnel index %s, walls lower %s upper %s",
			tostring(tpl and tpl.name), tostring(bridgeIndex), tostring(tunnelIndex), tostring(lowerWall),
			tostring(upperWall)))
	end
	return tpl ~= nil
end

local function makeContext()
	local c = api.type.Context.new()
	c.checkTerrainAlignment = true
	c.cleanupStreetGraph = true
	c.gatherBuildings = false
	c.gatherFields = true
	return c
end

-- a chain of edges: points {x, y, z}, tangents per point (direction x length is set per edge), kinds per edge
-- ("normal", "bridge", "tunnel", "fill", "cut": normal with a lower/upper wall on both sides); startNode = an
-- existing node entity for the first point (junction) or nil; street = the street (default: DATA.template)
local function makeProposal(points, directions, kinds, startNode, street)
	street = street or tpl
	local enum0 = api.type["enum"]
	local enum = api.type["enum"]
	local proposal = api.type.SimpleProposal.new()
	local newNodes, newEdges, ids = {}, {}, {}
	for k = 1, #points do
		if k == 1 and startNode then
			ids[k] = startNode
		else
			local nd = api.type.NodeAndEntity.new()
			nd.entity = -k
			nd.comp.position = vec3(points[k])
			newNodes[#newNodes + 1] = nd
			ids[k] = -k
		end
	end
	for k = 1, #points - 1 do
		local e = api.type.SegmentAndEntity.new()
		e.entity = -(#points + k)
		e.type = 0
		e.comp.node0 = ids[k]
		e.comp.node1 = ids[k + 1]
		e.comp.position0 = vec3(points[k])
		e.comp.position1 = vec3(points[k + 1])
		e.comp.tangent0 = vec3(directions[k][1])
		e.comp.tangent1 = vec3(directions[k][2])
		if kinds[k] == "bridge" and bridgeIndex then
			e.comp.type = enum.BaseEdgeType.BRIDGE
			e.comp.typeIndex = bridgeIndex
		elseif kinds[k] == "tunnel" and tunnelIndex then
			e.comp.type = enum.BaseEdgeType.TUNNEL
			e.comp.typeIndex = tunnelIndex
		else
			e.comp.type = enum.BaseEdgeType.NORMAL
			e.comp.typeIndex = 1          -- as the road mod (and the game's track builder)
		end
		local wall = (kinds[k] == "fill" and lowerWall) or (kinds[k] == "cut" and upperWall) or nil
		if wall then
			e.comp.edgeDecorations = { { wall, true }, { wall, false } }
		end
		e.comp.laneConfigs = street.lanes
		e.comp.roadTemplate = street.name
		e.comp.roadStyle = street.style
		e.comp.roadType = enum.RoadType.STREET
		newEdges[#newEdges + 1] = e
	end
	proposal.streetProposal.nodesToAdd = newNodes
	proposal.streetProposal.edgesToAdd = newEdges
	-- junction (3 edges afterwards): an empty node configuration, without it the game crashes (as dhe_path_streets)
	if startNode and #api.engine.system.streetSystem.getNodeStreetSegments(startNode) >= 2 then
		local cfg = api.type.BaseNodeLaneConnectionAndEntity.new()
		cfg.entity = startNode
		pcall(function() cfg.comp.trafficLightPreference = enum0.TrafficLightPreference.NO end)
		proposal.streetProposal.nodeConfigsToAdd = { cfg }
		if api.engine.getComponent(startNode, api.type.ComponentType.BASE_NODE_CONFIG) then
			proposal.streetProposal.nodeConfigsToRemove = { startNode }
		end
	end
	return proposal
end

-- straight pieces: tangent = chord (with its height difference)
local function straightDirections(points)
	local d = {}
	for k = 1, #points - 1 do
		local chord = sub(points[k + 1], points[k])
		d[k] = { chord, chord }
	end
	return d
end

-- check, log the game's messages, build (no callback: GameScripts may not use them)
local function build(name, proposal)
	local ok, err = pcall(function()
		local es = api.engine.util.proposal.makeProposalData(proposal, makeContext()).errorState
		local parts = {}
		for _, m in pairs(es.messages or {}) do parts[#parts + 1] = tostring(m) end
		for _, m in pairs(es.warnings or {}) do parts[#parts + 1] = "warning " .. tostring(m) end
		log(string.format("%s: check %s %s", name, es.critical and "REJECTED" or "ok", table.concat(parts, "; ")))
		if es.critical then return end
		api.cmd.sendCommand(api.cmd.makeWorldBuildProposalCmd(proposal, makeContext(), true, false))
		log(name .. ": sent")
	end)
	if not ok then log(name .. ": error " .. tostring(err)) end
end

-- T1/T2: straight line with a constant grade between the ground at both ends; T1 with bridges/tunnels where the line
-- is far above/below the ground, T2 the same line as normal street only (the game's own cut and fill)
local function profileLine(origin, dir, withStructures, withWalls)
	local a, b = onGround(origin), onGround(add(origin, mul(dir, DATA.lineLength)))
	local n = math.max(1, math.floor(DATA.lineLength / DATA.edgeLength + 0.5))
	local points, kinds, counts = {}, {}, { normal = 0, bridge = 0, tunnel = 0, fill = 0, cut = 0 }
	for k = 0, n do
		points[#points + 1] = add(a, mul(sub(b, a), k / n))
	end
	for k = 1, n do
		local mid = mul(add(points[k], points[k + 1]), 0.5)
		local above = mid[3] - ground(mid)
		local kind = "normal"
		if withStructures and above > DATA.bridgeAbove then kind = "bridge" end
		if withStructures and above < -DATA.tunnelBelow then kind = "tunnel" end
		if withWalls and above > DATA.wallFrom then kind = "fill" end
		if withWalls and above < -DATA.wallFrom then kind = "cut" end
		kinds[k] = kind
		counts[kind] = counts[kind] + 1
	end
	log(string.format("profile line %s: grade %.1f %%, edges normal %d, bridge %d, tunnel %d, fill %d, cut %d",
		withStructures and "with structures" or (withWalls and "with walls" or "plain"),
		(b[3] - a[3]) / DATA.lineLength * 100, counts.normal, counts.bridge, counts.tunnel, counts.fill, counts.cut))
	return points, straightDirections(points), kinds
end

-- T4: hairpin with radius r: leg out along dir, half circle, leg back; heights follow the ground (as the road mod)
local function hairpin(base, dir, normal, r)
	local points, dirs, kinds = {}, {}, {}
	local legEdges = math.max(1, math.floor(DATA.legLength / DATA.edgeLength + 0.5))
	for k = 0, legEdges do
		points[#points + 1] = add(base, mul(dir, DATA.legLength * k / legEdges))
	end
	local centre = add(add(base, mul(dir, DATA.legLength)), mul(normal, -r))
	local arcEdges = math.max(4, math.ceil(math.pi * r / DATA.edgeLength))
	local step = math.pi / arcEdges
	local arcTangent = 4 * math.tan(step / 4) * r          -- cubic Hermite close to a circular arc
	local arcStart = #points
	for k = 1, arcEdges do
		local angle = k * step                                 -- from +normal over +dir to -normal
		points[#points + 1] = add(centre, add(mul(normal, r * math.cos(angle)), mul(dir, r * math.sin(angle))))
	end
	for k = 1, legEdges do
		points[#points + 1] = add(add(base, mul(normal, -2 * r)), mul(dir, DATA.legLength * (1 - k / legEdges)))
	end
	for k = 1, #points do points[k] = onGround(points[k]) end
	for k = 1, #points - 1 do
		local chord = sub(points[k + 1], points[k])
		if k > arcStart - 1 and k < arcStart + arcEdges then
			local a0 = (k - arcStart) * step
			local t0 = add(mul(normal, -math.sin(a0)), mul(dir, math.cos(a0)))
			local t1 = add(mul(normal, -math.sin(a0 + step)), mul(dir, math.cos(a0 + step)))
			local rise = chord[3] / len({ chord[1], chord[2] }) * arcTangent
			dirs[k] = { add(mul(t0, arcTangent), { 0, 0, rise }), add(mul(t1, arcTangent), { 0, 0, rise }) }
		else
			dirs[k] = { chord, chord }
		end
		kinds[k] = "normal"
	end
	return points, dirs, kinds
end

local function runTests(edge, pending)
	local c = api.engine.getComponent(edge, api.type.ComponentType.BASE_EDGE)
	local n0 = api.engine.getComponent(c.node0, api.type.ComponentType.BASE_NODE).position
	local n1 = api.engine.getComponent(c.node1, api.type.ComponentType.BASE_NODE).position
	local origin = { n1.x, n1.y, n1.z }
	local d = sub({ n1.x, n1.y, 0 }, { n0.x, n0.y, 0 })
	local dir = mul(d, 1 / math.max(len(d), 1e-6))
	local normal = { -dir[2], dir[1], 0 }
	log(string.format("marker at x=%.0f y=%.0f, direction %.2f %.2f", origin[1], origin[2], dir[1], dir[2]))

	local start1 = add(origin, mul(dir, 20))
	build("T1 profile with bridges/tunnels", makeProposal(profileLine(start1, dir, true)))

	local start2 = add(start1, mul(normal, DATA.lineOffset))
	local p2, d2, k2 = profileLine(start2, dir, false)
	local middle = math.floor(#p2 / 2)
	build("T2 profile without structures", makeProposal(p2, d2, k2))
	pending[#pending + 1] = { at = p2[middle + 1], normal = normal, tries = 0 }

	local start5 = add(start2, mul(normal, DATA.lineOffset))
	if steepTpl then
		local p5, d5, k5 = profileLine(start5, dir, false, true)
		build("T5 profile, steep street with retaining walls", makeProposal(p5, d5, k5, nil, steepTpl))
	else
		log("T5 skipped: steep street of DHE_Walls_Tracks_Streets not found")
	end

	local offset = -DATA.lineOffset
	for _, r in ipairs(DATA.radii) do
		local base = add(start1, mul(normal, offset))
		build(string.format("T4 hairpin radius %g m", r), makeProposal(hairpin(base, dir, normal, r)))
		offset = offset - (2 * r + DATA.lineOffset)
	end
end

-- the existing node nearest to p within 1.5 m (octree search, as dhe_path_streets)
local function findNode(p)
	local best, bestDist, at = nil, 1.5, nil
	for _, e in pairs(api.engine.util.octree.findEntitiesInCircle(api.type.Vec2f.new(p[1], p[2]), 1.5,
			api.type.ComponentType.BASE_NODE)) do
		local nd = api.engine.getComponent(e, api.type.ComponentType.BASE_NODE)
		if nd then
			local dist = len(sub({ nd.position.x, nd.position.y, 0 }, { p[1], p[2], 0 }))
			if dist < bestDist then best, bestDist, at = e, dist, { nd.position.x, nd.position.y, nd.position.z } end
		end
	end
	return best, bestDist, at
end

-- T3: a branch from the middle node of T2 (junction), once T2 is built
local function buildBranches(pending)
	for k = #pending, 1, -1 do
		local p = pending[k]
		p.tries = p.tries + 1
		local best, bestDist, at = findNode(p.at)
		if not best and p.tries < JUNCTION_TRIES then
			-- T2 not built yet: try again next tick
		elseif not best then
			table.remove(pending, k)
			log("T3: the middle node of T2 was not found (T2 not built?)")
		else
			table.remove(pending, k)
			local b = onGround(add(at, mul(p.normal, DATA.branchLength)))
			local points = { at, b }
			build(string.format("T3 junction to an existing node (%.1f m from the middle)", bestDist),
				makeProposal(points, straightDirections(points), { "normal" }, best))
		end
	end
end

-- streets of the player that are new since the last look, except the test roads themselves; on the first look
-- every existing street only counts as seen
local function newMarkers(s)
	local found, first, existing = {}, s.seen == nil, 0
	s.seen = s.seen or {}
	local own = { [tpl.name] = true }
	if steepTpl then own[steepTpl.name] = true end
	for _, edges in pairs(api.engine.system.streetSystem.getNode2StreetEdgeMap()) do
		for _, e in pairs(edges) do
			local key = tostring(e)
			if not s.seen[key] and api.engine.getComponent(e, api.type.ComponentType.PLAYER_OWNED) then
				s.seen[key] = true
				local c = api.engine.getComponent(e, api.type.ComponentType.BASE_EDGE)
				if first then
					existing = existing + 1
				elseif c and not own[tostring(c.roadTemplate)] then
					log("new street of the player: " .. tostring(c.roadTemplate))
					found[#found + 1] = e
				end
			end
		end
	end
	if first then
		log(string.format("ready: build a short street (any type); %d existing streets of the player ignored", existing))
	end
	return found
end

function data()
	return {
		update = function(params, state, dt)
			local s = state:get() or {}
			s.calls = (s.calls or 0) + 1
			s.pending = s.pending or {}
			if s.calls % SCAN_CALLS ~= 0 and #s.pending == 0 then
				state:set(s)
				return
			end
			quiet = s.logged
			if not resolve() then
				log("street type not found: " .. DATA.template)
				state:set(s)
				return
			end
			if not s.logged then
				log(string.format("first scan after %d calls (dt %s)", s.calls, tostring(dt)))
				s.logged = true
			end
			if #s.pending > 0 then
				local ok, err = pcall(buildBranches, s.pending)
				if not ok then log("T3: error " .. tostring(err)) end
			end
			if s.calls % SCAN_CALLS == 0 then
				local ok, found = pcall(newMarkers, s)
				if not ok then
					log("search for new streets failed: " .. tostring(found))
				else
					for _, e in ipairs(found) do
						local done, err = pcall(runTests, e, s.pending)
						if not done then log("error: " .. tostring(err)) end
					end
				end
			end
			state:set(s)
		end,
	}
end
