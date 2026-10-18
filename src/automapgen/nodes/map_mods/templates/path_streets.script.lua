-- Made by tf3gen (path street mod) - builds field tracks and hiking paths as streets
local DATA = {
	version = "@VERSION@",
	flipY = @FLIPY@,
	aspect = @ASPECT@,
	modId = "@MODID@",
	clear = @CLEAR@,
	templates = { @TEMPLATES@ },
	nodes = "@NODES@",
	chains = "@CHAINS@",
	kinds = "@KINDS@",
	junctions = "@JUNCTIONS@",
}

local WAIT_TICKS = 20     -- build only after the game has run this many steps
local runningTicks = 0
local nodes, chains, kinds, isJunction = nil, nil, nil, nil
local tpls = nil

local function log(msg)
	print("[dhe_path_streets] " .. msg)
end

local function parse()
	nodes = {}
	for a in string.gmatch(DATA.nodes, "%S+") do
		nodes[#nodes + 1] = tonumber(a) / 1e7
	end
	chains = {}
	for c in string.gmatch(DATA.chains, "[^|]+") do
		local t = {}
		for a in string.gmatch(c, "%S+") do
			t[#t + 1] = tonumber(a)
		end
		chains[#chains + 1] = t
	end
	kinds = {}
	for a in string.gmatch(DATA.kinds, "%S+") do
		kinds[#kinds + 1] = tonumber(a)
	end
	isJunction = {}
	for a in string.gmatch(DATA.junctions, "%S+") do
		isJunction[tonumber(a)] = true
	end
end

-- find the own street types (spelling of the mod path unverified, hence several variants)
local function resolveTemplates()
	tpls = {}
	for i, file in ipairs(DATA.templates) do
		local path = "infrastructure/street/dhe/" .. file .. ".street_template"
		for _, name in ipairs({ DATA.modId .. "::/" .. path, "/" .. path, "::/" .. path, path }) do
			local ok, id = pcall(api.res.streetTemplateRep.find, name)
			log("street type " .. name .. " -> " .. tostring(ok and id or id))
			if ok and id and id >= 0 then
				local t = api.res.streetTemplateRep.get(id)
				tpls[i] = { name = name, id = id, style = t.streetStyle, lanes = t.laneConfigs }
				break
			end
		end
		if not tpls[i] then
			log("street type not found: " .. file)
			return false
		end
	end
	return true
end

local function worldPos(i, bb)
	local u, v = nodes[2 * i - 1], nodes[2 * i]
	local x = bb.min.x + u * (bb.max.x - bb.min.x)
	local y
	if DATA.flipY then
		y = bb.min.y + v * (bb.max.y - bb.min.y)
	else
		y = bb.max.y - v * (bb.max.y - bb.min.y)
	end
	return { x, y, api.engine.terrain.getHeightAt(api.type.Vec2f.new(x, y)) }
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
		c.gatherBuildings = false
		c.gatherFields = true
		return c
	end)
	return ok and ctx or nil
end

-- existing node at position p (junction, built by an earlier section): entity, position
local function findNode(p)
	local best, bestD, bestPos = nil, 1.5, nil
	local ok, err = pcall(function()
		for _, e in pairs(api.engine.util.octree.findEntitiesInCircle(api.type.Vec2f.new(p[1], p[2]), 1.5,
				api.type.ComponentType.BASE_NODE)) do
			local nd = api.engine.getComponent(e, api.type.ComponentType.BASE_NODE)
			if nd then
				local q = nd.position
				local d = math.sqrt((q.x - p[1]) ^ 2 + (q.y - p[2]) ^ 2)
				if d < bestD then
					best, bestD, bestPos = e, d, { q.x, q.y, q.z }
				end
			end
		end
	end)
	if not ok then
		log("node search failed: " .. tostring(err))
	end
	return best, bestPos
end

-- piece a..b of a section as a build proposal (like the road mod); ends[1]/ends[n] = existing nodes
local function makeProposal(ci, a, b, bb, ends)
	local enum = api.type["enum"]
	local chain, tpl = chains[ci], tpls[kinds[ci]]
	local pos = {}
	for k = a, b do
		pos[#pos + 1] = worldPos(chain[k], bb)
	end
	local n = #pos
	ends = ends or {}
	for k, e in pairs(ends) do
		pos[k] = e[2]
	end
	local dir = {}
	for k = 1, n do
		dir[k] = sub(pos[math.min(k + 1, n)], pos[math.max(k - 1, 1)])
	end
	local proposal = api.type.SimpleProposal.new()
	local newNodes, newEdges, ref = {}, {}, {}
	local nextId = 0
	for k = 1, n do
		if ends[k] then
			ref[k] = ends[k][1]
		else
			nextId = nextId + 1
			local nd = api.type.NodeAndEntity.new()
			nd.entity = -nextId
			nd.comp.position = vec(pos[k])
			newNodes[#newNodes + 1] = nd
			ref[k] = -nextId
		end
	end
	for k = 1, n - 1 do
		local l = len(sub(pos[k + 1], pos[k]))
		local e = api.type.SegmentAndEntity.new()
		nextId = nextId + 1
		e.entity = -nextId
		e.type = 0
		e.comp.node0 = ref[k]
		e.comp.node1 = ref[k + 1]
		e.comp.position0 = vec(pos[k])
		e.comp.position1 = vec(pos[k + 1])
		e.comp.tangent0 = vec(scaled(dir[k], l))
		e.comp.tangent1 = vec(scaled(dir[k + 1], l))
		e.comp.type = enum.BaseEdgeType.NORMAL
		e.comp.typeIndex = 1
		e.comp.laneConfigs = tpl.lanes
		e.comp.roadTemplate = tpl.name
		e.comp.roadStyle = tpl.style
		e.comp.roadType = enum.RoadType.STREET
		pcall(function() e.comp.roadDevelopmentLocked = true end)
		newEdges[k] = e
	end
	proposal.streetProposal.nodesToAdd = newNodes
	proposal.streetProposal.edgesToAdd = newEdges
	-- junction (at least 3 edges afterwards): empty default configuration, without it the game crashes
	local configs, removeConfigs = {}, {}
	for k, e in pairs(ends) do
		local id = e[1]
		local segs = api.engine.system.streetSystem.getNodeStreetSegments(id)
		if #segs >= 2 then
			local cfg = api.type.BaseNodeLaneConnectionAndEntity.new()
			cfg.entity = id
			pcall(function() cfg.comp.trafficLightPreference = enum.TrafficLightPreference.NO end)
			configs[#configs + 1] = cfg
			if api.engine.getComponent(id, api.type.ComponentType.BASE_NODE_CONFIG) then
				removeConfigs[#removeConfigs + 1] = id
			end
		end
	end
	if #configs > 0 then
		proposal.streetProposal.nodeConfigsToAdd = configs
	end
	if #removeConfigs > 0 then
		proposal.streetProposal.nodeConfigsToRemove = removeConfigs
	end
	return proposal, pos, #configs
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
		return "check not possible: " .. tostring(err), true
	end
	return problem, false
end

local function ownTemplate(t)
	for _, tpl in pairs(tpls) do
		if t == tpl.id or t == tpl.name then
			return true
		end
	end
	return string.find(tostring(t), "dhe_", 1, true) ~= nil
end

-- is there another street (town street, country road) within DATA.clear? Own paths are in the plan
local function nearStreet(p)
	local c = api.type.Vec2f.new(p[1], p[2])
	local ok, found = pcall(function()
		for _, e in pairs(api.engine.util.octree.findEntitiesInCircle(c, DATA.clear, api.type.ComponentType.BASE_EDGE)) do
			local edge = api.engine.getComponent(e, api.type.ComponentType.BASE_EDGE)
			if not edge or not ownTemplate(edge.roadTemplate) then
				return true
			end
		end
		return false
	end)
	return not ok or found
end

-- split piece a..b into pieces without streets nearby; true = free and unchanged
local function splitFree(s, a, b, bb)
	local chain = chains[s.chain]
	local free = {}
	local all = true
	for k = a, b do
		local p = worldPos(chain[k], bb)
		free[k] = not nearStreet(p)
		if k > a and free[k] and free[k - 1] then
			local q = worldPos(chain[k - 1], bb)
			free[k] = not nearStreet({ (p[1] + q[1]) / 2, (p[2] + q[2]) / 2 })
		end
		all = all and free[k]
	end
	if all then
		return true
	end
	local runs = {}
	local k = a
	while k <= b do
		if free[k] then
			local e = k
			while e < b and free[e + 1] do
				e = e + 1
			end
			if e > k then
				runs[#runs + 1] = { k, e }
			end
			k = e + 1
		else
			k = k + 1
		end
	end
	for i = #runs, 1, -1 do
		table.insert(s.pieces, 1, runs[i])
	end
	s.gaps = s.gaps + 1
	local p = worldPos(chain[a], bb)
	log(string.format("  street nearby, section %d at x=%.0f y=%.0f: %d pieces remain", s.chain, p[1], p[2], #runs))
	return false
end

-- build one piece; if the game rejects it, split it in halves (one edge between them stays a gap)
local function step(s, bb)
	local piece = table.remove(s.pieces, 1)
	local a, b = piece[1], piece[2]
	if not piece[3] then
		if not splitFree(s, a, b, bb) then
			return
		end
	end
	local chain = chains[s.chain]
	local ends = {}
	for _, k in ipairs({ a, b }) do
		if (k == 1 or k == #chain) and isJunction[chain[k]] then
			local id, p = findNode(worldPos(chain[k], bb))
			if id then
				ends[k - a + 1] = { id, p }
			end
		end
	end
	local proposal, pos, nConfigs = makeProposal(s.chain, a, b, bb, ends)
	local problem, exception = check(proposal)
	if exception then
		-- exception in the check (assertion): do not split further, every further check risks a crash
		s.gaps = s.gaps + 1
		log(string.format("  gap in section %d at x=%.0f y=%.0f (exception): %s", s.chain, pos[1][1], pos[1][2], problem))
		return
	end
	if not problem then
		api.cmd.sendCommand(api.cmd.makeWorldBuildProposalCmd(proposal, makeContext(), true, false))
		s.edges = s.edges + (b - a)
		local joined = 0
		for _ in pairs(ends) do
			joined = joined + 1
		end
		if joined > 0 then
			s.joined = (s.joined or 0) + joined
			log(string.format("  section %d connected to %d existing nodes (%d junctions)", s.chain, joined, nConfigs))
		end
		return
	end
	if b - a >= 3 then
		local m = math.floor((a + b) / 2)
		table.insert(s.pieces, 1, { m + 1, b, true })
		table.insert(s.pieces, 1, { a, m, true })
	else
		s.gaps = s.gaps + 1
		log(string.format("  gap in section %d at x=%.0f y=%.0f: %s", s.chain, pos[1][1], pos[1][2], problem))
	end
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
				s = { version = DATA.version, started = false, finished = false, chain = 0, pieces = {},
					edges = 0, gaps = 0 }
			end
			if s.finished then
				return
			end
			if not nodes then
				parse()
			end
			if not s.started then
				local bb = api.engine.terrain.getBoundingBox()
				local aspect = (bb.max.x - bb.min.x) / (bb.max.y - bb.min.y)
				log(string.format("start. map %.0f x %.0f m, %d sections",
					bb.max.x - bb.min.x, bb.max.y - bb.min.y, #chains))
				if math.abs(aspect / DATA.aspect - 1) > 0.02 then
					log(string.format("aspect ratio does not match (map %.3f, plan %.3f) - nothing built", aspect, DATA.aspect))
					s.finished = true
					state:set(s)
					return
				end
				s.started = true
				state:set(s)
			end
			if not tpls and not resolveTemplates() then
				s.finished = true
				state:set(s)
				return
			end

			-- next piece
			if #s.pieces == 0 then
				if s.chain >= #chains then
					log(string.format("done: %d edges built, %d gaps, %d connections at junctions", s.edges, s.gaps,
					s.joined or 0))
					s.finished = true
					state:set(s)
					return
				end
				s.chain = s.chain + 1
				s.pieces = { { 1, #chains[s.chain] } }
				log(string.format("section %d/%d (%d edges)", s.chain, #chains, #chains[s.chain] - 1))
			end
			local ok, err = pcall(step, s, api.engine.terrain.getBoundingBox())
			if not ok then
				log("error in section " .. s.chain .. ": " .. tostring(err))
				s.pieces = {}
				s.gaps = s.gaps + 1
			end
			state:set(s)
		end,
	}
end
