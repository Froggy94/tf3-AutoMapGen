-- Made by tf3-AutoMapGen (road mod) - builds the planned road network: planned heights, bridges, tunnels, junctions.
-- Building after TPF3 Map Studio's import mod (MIT, github.com/sharpi13822/TPF3-Map-Studio).
local DATA = {
	version = "@VERSION@",
	flipY = @FLIPY@,
	aspect = @ASPECT@,
	template = "@TEMPLATE@",
	structures = @STRUCTURES@,    -- bridge types by length, then the tunnel type (ends of the file names)
	marker = "@MARKER@",
	autoStart = @AUTOSTART@,
	removeBuildings = @REMOVE@,
	nodes = "@NODES@",            -- per node: u v (map fractions x 1e7) and game height (cm)
	ways = "@WAYS@",              -- per way: kind (0 normal, 1 bridge, 2 tunnel), structure (0 = none), nodes
}

local WAIT_TICKS = 20     -- check only after the game has run this many steps
local MARKER_TICKS = 50   -- look for the start signal this often
local SAME_NODE_M = 1.0   -- an existing street node this close is the planned node (junction)
local OFFSET_SAMPLES = 200
local runningTicks = 0
local nodes, ways, uses, tpl, structureIds = nil, nil, nil, nil, {}
local startEvent = false

local function log(msg)
	print("[dhe_roads] " .. msg)
end

local function parse()
	nodes = {}
	local n = {}
	for a in string.gmatch(DATA.nodes, "%S+") do
		n[#n + 1] = tonumber(a)
	end
	for i = 1, #n, 3 do
		nodes[#nodes + 1] = { n[i] / 1e7, n[i + 1] / 1e7, n[i + 2] / 100 }
	end
	ways, uses = {}, {}
	for w in string.gmatch(DATA.ways, "[^|]+") do
		local t = {}
		for a in string.gmatch(w, "%S+") do
			t[#t + 1] = tonumber(a)
		end
		local way = { kind = t[1], structure = t[2], nodes = {} }
		for k = 3, #t do
			way.nodes[#way.nodes + 1] = t[k]
			uses[t[k]] = (uses[t[k]] or 0) + 1
		end
		ways[#ways + 1] = way
	end
end

local function resolveTemplate()
	for _, prefix in ipairs({ "::/infrastructure/", "/infrastructure/" }) do
		local id = api.res.streetTemplateRep.find(prefix .. DATA.template)
		if id and id >= 0 then
			local t = api.res.streetTemplateRep.get(id)
			tpl = { name = prefix .. DATA.template, style = t.streetStyle, lanes = t.laneConfigs }
			log("street type: " .. tpl.name)
			return true
		end
	end
	log("street type not found: " .. DATA.template)
	return false
end

-- bridge and tunnel types: id in their repository by the end of the file name
local function resolveStructures()
	local count = #DATA.structures
	for i, ending in ipairs(DATA.structures) do
		local rep = (i == count) and api.res.tunnelTypeRep or api.res.bridgeTypeRep
		local ok, all = pcall(rep.getAll)
		if ok and all then
			for id, name in pairs(all) do
				if type(name) == "string" and name:sub(-#ending) == ending then
					structureIds[i] = id
					log("structure " .. ending .. ": id " .. tostring(id))
				end
			end
		end
		if structureIds[i] == nil then
			log("structure type not found: " .. ending .. " (its ways are built as normal streets)")
		end
	end
end

-- start signal: a street of the marker type built by the player
local function findMarker()
	local found = nil
	local ok, err = pcall(function()
		for _, edges in pairs(api.engine.system.streetSystem.getNode2StreetEdgeMap()) do
			for _, e in pairs(edges) do
				local c = api.engine.getComponent(e, api.type.ComponentType.BASE_EDGE)
				if c and string.find(tostring(c.roadTemplate), DATA.marker, 1, true)
					and api.engine.getComponent(e, api.type.ComponentType.PLAYER_OWNED) then
					found = e
					return
				end
			end
		end
	end)
	if not ok then
		log("search for the start signal failed: " .. tostring(err))
	end
	return found
end

local function worldXY(i, bb)
	local u, v = nodes[i][1], nodes[i][2]
	local x = bb.min.x + u * (bb.max.x - bb.min.x)
	local y
	if DATA.flipY then
		y = bb.min.y + v * (bb.max.y - bb.min.y)
	else
		y = bb.max.y - v * (bb.max.y - bb.min.y)
	end
	return x, y
end

local function worldPos(i, bb, offset)
	local x, y = worldXY(i, bb)
	return { x, y, nodes[i][3] + offset }
end

-- constant offset between the planned heights and the game terrain, measured on the nodes of normal ways
local function heightOffset(bb)
	local diffs = {}
	local total = 0
	for _, w in ipairs(ways) do
		if w.kind == 0 then
			total = total + #w.nodes
		end
	end
	local step = math.max(1, math.floor(total / OFFSET_SAMPLES))
	local k = 0
	for _, w in ipairs(ways) do
		if w.kind == 0 then
			for _, i in ipairs(w.nodes) do
				k = k + 1
				if k % step == 0 then
					local x, y = worldXY(i, bb)
					diffs[#diffs + 1] = api.engine.terrain.getHeightAt(api.type.Vec2f.new(x, y)) - nodes[i][3]
				end
			end
		end
	end
	if #diffs == 0 then
		return 0
	end
	table.sort(diffs)
	return diffs[math.floor((#diffs + 1) / 2)]
end

-- an existing street node at the position (a node built for an earlier way): entity and position, else nil
local function existingNode(x, y)
	local found = nil
	pcall(function()
		for node, _ in pairs(api.engine.system.streetSystem.getNode2SegmentMap()) do
			local c = api.engine.getComponent(node, api.type.ComponentType.BASE_NODE)
			if c and math.abs(c.position.x - x) < SAME_NODE_M and math.abs(c.position.y - y) < SAME_NODE_M then
				found = { entity = node, pos = { c.position.x, c.position.y, c.position.z } }
				return
			end
		end
	end)
	return found
end

local function vec(p)
	return api.type.Vec3f.new(p[1], p[2], p[3])
end

local function sub(a, b)
	return { a[1] - b[1], a[2] - b[2], a[3] - b[3] }
end

local function len(a)
	return math.sqrt(a[1] * a[1] + a[2] * a[2] + a[3] * a[3])
end

local function scaled(a, l)
	local n = len(a)
	if n < 1e-6 then
		return { 0, 0, 0 }
	end
	return { a[1] / n * l, a[2] / n * l, a[3] / n * l }
end

local function makeContext()
	local ok, ctx = pcall(function()
		local c = api.type.Context.new()
		c.checkTerrainAlignment = true
		c.cleanupStreetGraph = true
		c.gatherBuildings = DATA.removeBuildings
		c.gatherFields = true
		pcall(function() c.player = api.engine.util.getPlayer() end)
		return c
	end)
	return ok and ctx or nil
end

-- edge type and type index of a way: normal, or bridge/tunnel with its structure type
local function edgeType(way)
	local enum = api.type["enum"]
	local id = structureIds[way.structure]
	if way.kind == 1 and id ~= nil then
		return enum.BaseEdgeType.BRIDGE, id
	elseif way.kind == 2 and id ~= nil then
		return enum.BaseEdgeType.TUNNEL, id
	end
	return enum.BaseEdgeType.NORMAL, 1
end

-- piece a..b (positions within the way) as a build proposal, like the game itself
-- (base/content/mission/tasks/auto_builder/track_builder.tl); the ends attach to existing nodes (junctions)
local function makeProposal(way, a, b, bb, offset)
	local enum = api.type["enum"]
	local all = way.nodes
	local pos = {}
	for k = 1, #all do
		pos[k] = worldPos(all[k], bb, offset)
	end
	local ids, existing = {}, 0
	local proposal = api.type.SimpleProposal.new()
	local newNodes, newEdges = {}, {}
	for k = a, b do
		-- only shared nodes and the ends of split pieces can exist already
		local found = nil
		if (k == a or k == b) and (uses[all[k]] > 1 or (k ~= 1 and k ~= #all)) then
			found = existingNode(pos[k][1], pos[k][2])
		end
		if found then
			ids[k] = found.entity
			pos[k] = found.pos
			existing = existing + 1
		else
			local nd = api.type.NodeAndEntity.new()
			nd.entity = -(#newNodes + 1)
			nd.comp.position = vec(pos[k])
			newNodes[#newNodes + 1] = nd
			ids[k] = nd.entity
		end
	end
	local edgeKind, typeIndex = edgeType(way)
	for k = a, b - 1 do
		local l = len(sub(pos[k + 1], pos[k]))
		local d0 = sub(pos[math.min(k + 1, #all)], pos[math.max(k - 1, 1)])
		local d1 = sub(pos[math.min(k + 2, #all)], pos[k])
		local e = api.type.SegmentAndEntity.new()
		e.entity = -(#newNodes + #newEdges + 1)
		e.type = 0
		e.comp.node0 = ids[k]
		e.comp.node1 = ids[k + 1]
		e.comp.position0 = vec(pos[k])
		e.comp.position1 = vec(pos[k + 1])
		e.comp.tangent0 = vec(scaled(d0, l))
		e.comp.tangent1 = vec(scaled(d1, l))
		e.comp.type = edgeKind
		e.comp.typeIndex = typeIndex
		e.comp.laneConfigs = tpl.lanes
		e.comp.roadTemplate = tpl.name
		e.comp.roadStyle = tpl.style
		e.comp.roadType = enum.RoadType.STREET
		newEdges[#newEdges + 1] = e
	end
	proposal.streetProposal.nodesToAdd = newNodes
	proposal.streetProposal.edgesToAdd = newEdges
	return proposal, pos, existing
end

-- check before building: nil = fine, else the problem
local function check(proposal)
	local problem = nil
	local ok, err = pcall(function()
		local es = api.engine.util.proposal.makeProposalData(proposal, makeContext()).errorState
		if es.critical then
			local parts = {}
			for _, m in pairs(es.messages or {}) do
				parts[#parts + 1] = tostring(m)
			end
			problem = table.concat(parts, "; ")
		end
	end)
	if not ok then
		problem = "check not possible: " .. tostring(err)
	end
	return problem
end

-- build one piece; if the game rejects it, split it in halves sharing the middle node (the second half attaches to
-- it); a piece of one edge that still fails stays a gap
local function step(s, bb)
	local piece = table.remove(s.pieces, 1)
	local way = ways[s.way]
	local a, b = piece[1], piece[2]
	local proposal, pos, existing = makeProposal(way, a, b, bb, s.offset)
	local problem = check(proposal)
	if not problem then
		api.cmd.sendCommand(api.cmd.makeWorldBuildProposalCmd(proposal, makeContext(), true, false))
		s.edges = s.edges + (b - a)
		s.junctions = s.junctions + existing
		return
	end
	if b - a >= 2 then
		local m = math.floor((a + b) / 2)
		table.insert(s.pieces, 1, { m, b })
		table.insert(s.pieces, 1, { a, m })
	else
		s.gaps = s.gaps + 1
		log(string.format("  gap in way %d at x=%.0f y=%.0f: %s", s.way, pos[a][1], pos[a][2], problem))
	end
end

local function begin(s)
	local bb = api.engine.terrain.getBoundingBox()
	local aspect = (bb.max.x - bb.min.x) / (bb.max.y - bb.min.y)
	log(string.format("start. map %.0f x %.0f m, %d ways", bb.max.x - bb.min.x, bb.max.y - bb.min.y, #ways))
	if math.abs(aspect / DATA.aspect - 1) > 0.02 then
		log(string.format("aspect ratio does not match (map %.3f, plan %.3f) - nothing built", aspect, DATA.aspect))
		s.finished = true
		return
	end
	s.offset = heightOffset(bb)
	log(string.format("height offset game terrain - plan: %.2f m (added to the planned heights)", s.offset))
	s.started = true
end

function data()
	return {
		update = function(params, state, dt)
			if dt and dt > 0 then
				runningTicks = runningTicks + 1
			end
			if runningTicks < WAIT_TICKS then
				return
			end
			local s = state:get() or {}
			if s.version ~= DATA.version then
				s = { version = DATA.version, started = false, finished = false, way = 0, pieces = {},
					edges = 0, gaps = 0, junctions = 0, offset = 0 }
			end
			if s.finished then
				return
			end
			if not nodes then
				parse()
			end

			-- wait for the start signal
			if not s.started then
				if not startEvent and runningTicks % MARKER_TICKS ~= 0 then
					return
				end
				if not (DATA.autoStart or startEvent or findMarker()) then
					return
				end
				begin(s)
				state:set(s)
				if s.finished then
					return
				end
			end
			if not tpl then
				if not resolveTemplate() then
					s.finished = true
					state:set(s)
					return
				end
				resolveStructures()
			end

			-- next piece
			if #s.pieces == 0 then
				if s.way >= #ways then
					log(string.format("done: %d edges built, %d junctions attached, %d gaps", s.edges, s.junctions,
						s.gaps))
					s.finished = true
					state:set(s)
					return
				end
				s.way = s.way + 1
				s.pieces = { { 1, #ways[s.way].nodes } }
				log(string.format("way %d/%d (%s, %d edges)", s.way, #ways, ({ "normal", "bridge", "tunnel" })[
					ways[s.way].kind + 1], #ways[s.way].nodes - 1))
			end
			local ok, err = pcall(step, s, api.engine.terrain.getBoundingBox())
			if not ok then
				log("error in way " .. s.way .. ": " .. tostring(err))
				s.pieces = {}
				s.gaps = s.gaps + 1
			end
			state:set(s)
		end,
		handleEvent = function(params, state, src, id, name, param)
			if id == "dhe_roads" and name == "start" then
				startEvent = true
			end
		end,
	}
end
