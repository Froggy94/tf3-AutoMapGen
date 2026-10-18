-- Made by tf3gen (industry names mod)
local DATA = { version = "@VERSION@", towns = {
@TOWNS@
}, types = {
@TYPES@
} }
local ticks, nextCheck = 0, @WAIT@

local function log(s)
	print("[dhe_industry_names] " .. s)
end

local function trim(s)
	return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function nameOf(entity)
	local ok, n = pcall(api.engine.util.getEntityName, entity)
	return ok and n or nil
end

local function stemOf(entity)
	local ok, r = pcall(api.engine.util.getEntityNameStem, entity)
	if not ok or r == nil then
		return nil
	end
	if type(r) == "table" then
		return r[1]
	end
	return r
end

local function setName(entity, name)
	if entity and api.engine.entityExists(entity)
			and api.engine.getComponent(entity, api.type.ComponentType.NAME) then
		api.cmd.sendCommand(api.cmd.makeEntitySetNameCmd(entity, name))
		return true
	end
	return false
end

local function nearestTown(x, y)
	local best, bestD = nil, math.huge
	for _, t in ipairs(DATA.towns) do
		local d = (t[1] - x) ^ 2 + (t[2] - y) ^ 2
		if d < bestD then
			best, bestD = t[3], d
		end
	end
	return best
end

local function typeName(con, current, stem)
	if current and stem and stem ~= "" and current:sub(1, #stem) == stem then
		local rest = trim(current:sub(#stem + 1))
		if rest ~= "" then
			return rest, "game name"
		end
	end
	local key = con.fileName and con.fileName:match("([%w_]+)%.con$")
	return key and DATA.types[key] or key or "Industry", "table"
end

local function rename(s, full)
	s.done = s.done or {}
	local found, named, kept, examples = 0, 0, 0, 0
	for _, e in ipairs(api.engine.getEntitiesWithComponent(api.type.ComponentType.INDUSTRY)) do
		found = found + 1
		local key = tostring(e)
		local ind = api.engine.getComponent(e, api.type.ComponentType.INDUSTRY)
		local con = ind and api.engine.getComponent(ind.construction, api.type.ComponentType.CONSTRUCTION)
		if con and (full or s.done[key] == nil) then
			local current = nameOf(ind.construction) or nameOf(e)
			local mine = s.done[key]
			if mine ~= nil and current ~= mine.name then
				kept = kept + 1                     -- renamed in the game by the player: leave it
			else
				local typ, src = mine and mine.type or nil, "remembered"
				if not typ then
					typ, src = typeName(con, current, stemOf(ind.construction) or stemOf(e))
				end
				local town = nearestTown(con.transf[13], con.transf[14])
				if town then
					local name = town .. " " .. typ
					if name ~= current then
						setName(ind.construction, name)
						setName(e, name)
						named = named + 1
						if examples < @EXAMPLES@ then
							examples = examples + 1
							log(string.format("'%s' -> '%s' (type from %s)", tostring(current), name, src))
						end
					end
					s.done[key] = { name = name, type = typ }
				end
			end
		end
	end
	if named > 0 or full then
		log(string.format("%d industries, %d renamed, %d named by the player (kept), %d towns", found, named, kept,
			#DATA.towns))
	end
end

function data()
	return {
		update = function(params, state, dt)
			if dt and dt > 0 then
				ticks = ticks + 1
			end
			if ticks < nextCheck then
				return
			end
			nextCheck = ticks + @RECHECK@
			local s = state:get() or {}
			local full = s.version ~= DATA.version          -- new town list: rename all (except those named by the player)
			local ok, err = pcall(rename, s, full)
			if not ok then
				log("error: " .. tostring(err))
			end
			s.version = DATA.version
			state:set(s)
		end,
	}
end
