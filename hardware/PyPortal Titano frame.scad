include <BOSL2/std.scad>
include <BOSL2/shapes3d.scad>

Viewable_width = 75;
Viewable_depth = 50.4;
Surface_height = 1.5;
Bezel = 9.3;
PyPortal_total_height = 11;
Light_pipe = true;
MicroSD_cutout = true;
Standoff_height = 4;

$fn = ($preview ? 36 : 72);

if (false && $preview) {
	color("#339933")
	translate([0.8, -27.7, -4.5])
	rotate([-90, 0, 0])
	import("4444 PyPortal Titano.stl");
}

function total_width() = Viewable_width + Bezel * 2 + Surface_height * 2;
function total_depth() = Viewable_depth + Bezel * 2 + Surface_height * 2;

module usb_c_hole(depth) {
	hole_width = 10.5;
	hole_radius = 2;

	for (y = [-hole_width / 2 + hole_radius, hole_width / 2 - hole_radius]) {
		translate([0, y, 0])
		rotate([90, 0, 90])
		cylinder(r = hole_radius, h = Surface_height);
	}
	
	translate([Surface_height / 2, 0, 0])
	cube([Surface_height, hole_width - hole_radius * 2, hole_radius * 2], center = true);
}

module case() {
	render()
	difference() {
		translate([0, 0, Surface_height / 2])
		cube([
			total_width(),
			total_depth(),
			Surface_height
		], center = true);

		prismoid(
			size1 = [Viewable_width, Viewable_depth],
			size2 = [Viewable_width + Surface_height * 2, Viewable_depth + Surface_height * 2],
			h = Surface_height
		);
	}

	for (x = [-total_width() / 2 + 12, total_width() / 2 - 12]) {
		for (y = [
			-5 / 2 + total_depth() / 2 - Surface_height,
			5 / 2 - total_depth() / 2 + Surface_height
			]
		) {
			translate([x, y, -PyPortal_total_height])
			render()
			difference() {
				union() {
					translate([0, 0, 2])
					prismoid(
						size2 = [6, 0],
						size1 = [6, 6],
						h = 5,
						shift = [0, y > 0 ? 5 / 2 : -5 / 2]
					);
					
					translate([0, 0, 2 / 2])
					cube([6, 6, 2], center = true);
				}
				
				cylinder(d = 2.85, h = 3);
			}
		}
	}

	render()
	difference() {
		translate([0, 0, -PyPortal_total_height / 2])
		cube([
			Viewable_width + Bezel * 2 + Surface_height * 2,
			Viewable_depth + Bezel * 2 + Surface_height * 2,
			PyPortal_total_height
		], center = true);
		
		translate([0, 0, -PyPortal_total_height / 2])
		cube([
			Viewable_width + Bezel * 2,
			Viewable_depth + Bezel * 2,
			PyPortal_total_height
		], center = true);
		
		translate([total_width() / 2 - Surface_height, -18.2, -7.2])
		usb_c_hole();
		
		if (MicroSD_cutout) {
			translate([total_width() / 2 - Surface_height, -3.4, -6.5])
			rotate([90, 0, 90])
			prismoid(
				size1 = [11.5, 1.5],
				size2 = [11.5 + Surface_height * 2, 1.5 + Surface_height * 2],
				h = Surface_height
			);
		}

		if (Light_pipe) {
			translate([total_width() / 2 - Surface_height, 4.4, -2.5])
			rotate([90, 0, 90])
			cylinder(d = 1.8, h = Surface_height);
		}
	}

	for (x = [43.975, -42.375]) {
		for (y = [30.075, -30.25]) {
			translate([x, y, 0])
			render()
			translate([0, 0, -Standoff_height])
			difference() {
				cylinder(d = 4, h = Standoff_height);
				cylinder(d = 2.85, h = Standoff_height);
			}
		}
	}
}

module backplate() {
	reset_hole_x = 2.6;
	reset_hole_y = 23;

	render()
	difference() {
		translate([-total_width() / 2, -total_depth() / 2, -PyPortal_total_height - Surface_height])
		cube([total_width(), total_depth(), Surface_height]);
		
		for (x = [-total_width() / 2 + 12, total_width() / 2 - 12]) {
			for (y = [
				-5 / 2 + total_depth() / 2 - Surface_height,
				5 / 2 - total_depth() / 2 + Surface_height
				]
			) {
				translate([x, y, -PyPortal_total_height - Surface_height])
				cylinder(d = 3.3, h = Surface_height);
			}
		}
		
		translate([reset_hole_x, reset_hole_y, -PyPortal_total_height - Surface_height])
		cylinder(d = 1.6, h = Surface_height);
	
		translate([reset_hole_x - 2.8, reset_hole_y + 3, -PyPortal_total_height - Surface_height])
		mirror([1, 0, 0])
		linear_extrude(0.4)
		text("RESET ↓", font = "SF Compact Display:style=Bold", halign = "right", valign = "bottom", size = 5);
	}
	
	for (x = [-10 - 5 / 2, 10 + 5 / 2]) {
		translate([x, total_depth() / 4, -PyPortal_total_height - Surface_height - 5 / 2])
		render()
		difference() {
			union() {
			rotate([90, 0, 90])
				cylinder(d = 7, h = 5, center = true);
		
				translate([0, 0, 5 / 4])
				cube([5, 7, 7 / 2], center = true);
			}
			
			rotate([90, 0, 90])
			cylinder(d = 3.2, h = 5, center = true);
		}
	}
}

module stand() {
	size = 3.1;
	inset = 5;

	stand_depth = total_depth() - total_depth() / 4;
	y = -total_depth() / 2 + stand_depth / 2;
	z = -PyPortal_total_height - Surface_height - inset / 2;

	translate([0, y, z])
	render()
	difference() {
		union() {
			cube([20, stand_depth, size], center = true);
			
			translate([0, stand_depth / 2, 0])
			rotate([90, 0, 90])
			cylinder(d = size, h = 20 + inset * 2, center = true);
		}
		
		translate([0, size * 2, 0])
		cube([20 - size * 2 - 5, stand_depth, size * 2], center = true);
	}
}

//color("#ffffee") case();
//color("#eeeeff") backplate();
color("#ffeeff") stand();