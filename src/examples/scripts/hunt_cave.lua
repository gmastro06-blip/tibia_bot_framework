# examples/scripts/hunt_cave.lua - Versión con pausa check

function hunt_loop()
  set_waypoint("cave_entry")
  move_to(960, 540)
  wait(500)
  log("Hunting started")

  while true do
    if is_paused() then
      log("Bot pausado - Esperando resume")
      wait(1000)
    else
      local hp = get_hp()
      local mp = get_mp()
      log("HP actual: " .. hp)
      log("MP actual: " .. mp)

      if hp < 50 then
        heal_if_needed()
      end

      local target = select_target({})
      if target then
        log("Target: " .. target)
        attack(target)
      end

      wait(200)
    end
  end
end

hunt_loop()