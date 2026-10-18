function data()
return {
		availability = {
			yearFrom = 0,
			yearTo = 0,
		},
		description = {
			name = _("@NAME@"),
			description = _("@DESCRIPTION@"),
			icon = "::/infrastructure/street/country/country_old_small.tga",
			previewIcon = "::/infrastructure/street/country/country_old_small_preview.tga",
		},
		menuCategory = {
			categories = {
				{
					category = "roads_country",
					filterCategories = { },
					order = 9000,
				},
			},
		},
		roadType = "STREET",
		streetStyle = "@FILE@.street",
		laneConfigs = {
@LANES@		},
		borderGroundTex = "::/infrastructure/shared/none.gtex",
		sidewalkFillGroundTex = "::/infrastructure/shared/none.gtex",
		fillGroundTex = "@FILE@_fill.gtex",
		cost = 0,
		maintenanceCost = 0,
		emissions = {
			noise = 0,
			pollution = 0,
			pollutionRadius = 0,
			radius = 0,
		},
		modifiers = {
			maxSpeedModifier = 0,
			noiseModifier = 0,
			pollutionModifier = 0,
		},
		maxSlope = @MAX_SLOPE@,
		maxSlopeBuild = @MAX_SLOPE@,
		maxSlopeShape = @MAX_SLOPE@,
		aiLock = true,
		simBuildable = false,
		country = true,
		priority = 0,
		defaultWithCrosswalk = false,
		pedestrianWalkPenalty = false,
		transportModesStreet = @STREET_MODES@,
		builderAudioRes = "::/gui/construction/sound/buildoze_street.builder_audio",
	}
end
