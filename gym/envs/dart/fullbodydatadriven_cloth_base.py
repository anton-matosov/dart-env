# This environment is created by Alexander Clegg (alexanderwclegg@gmail.com)

import numpy as np
from gym import utils
from gym.envs.dart.dart_cloth_env import *
import random
import time
import math

from pyPhysX.colors import *
import pyPhysX.pyutils as pyutils
from pyPhysX.pyutils import LERP
import pyPhysX.renderUtils
import pyPhysX.meshgraph as meshgraph
from pyPhysX.clothfeature import *

import OpenGL.GL as GL
import OpenGL.GLU as GLU
import OpenGL.GLUT as GLUT

class DartClothFullBodyDataDrivenClothBaseEnv(DartClothEnv, utils.EzPickle):
    def __init__(self, rendering=True, screensize=(1080,720), clothMeshFile="", clothMeshStateFile=None, clothScale=1.4, obs_size=0, simulateCloth=True, recurrency=0, SPDActionSpace=False, gravity=True):
        self.prefix = os.path.dirname(__file__)

        #rendering variables
        self.useOpenGL = rendering
        self.screenSize = screensize
        self.renderDARTWorld = True
        self.renderUI = True
        self.renderRewardsData = True
        self.avgtimings = {}

        #sim variables
        self.gravity = gravity
        self.dataDrivenJointLimts = True
        self.additionalAction = np.zeros(34) #added to input action for step
        self.SPD = None #no target yet
        self.SPDTarget = None #if set, reset calls setup on the SPDController and udates/queries it
        self.recurrency = recurrency
        self.actionTrajectory = []
        self.stateTraj = []
        self.totalTime = 0
        self.locked_foot = False

        #action graphing
        self.graphingActions = True
        self.actionTrajectory = []
        self.actionGraph = None
        self.actionGraphFoci = [6,7]
        self.updateDelay = 3
        self.lastUpdate = 3
        self.changedFocus = False

        #rewards data tracking
        self.rewardsData = renderUtils.RewardsData([],[],[],[])

        #output for rendering controls
        self.recordForRendering = False
        #self.recordForRenderingOutputPrefix = "saved_render_states/jacket/jacket"
        self.recordForRenderingOutputPrefix = "saved_render_states/lowerbody/lowerbody"

        #other tracking variables
        self.rewardTrajectory = [] #store all rewards since the last reset

        #violation graphing
        self.graphViolation = False
        self.violationGraphFrequency = 1

        #cloth "badness" graphing
        self.graphClothViolation = False
        self.saveClothViolationGraphOnReset = True
        self.clothViolationGraph = None
        if self.graphClothViolation:
            self.clothViolationGraph = pyutils.LineGrapher(title="Cloth Violation", numPlots=3)

        self.actuatedDofs = np.arange(34) # full upper body (discount the free root dofs)
        for i in range(len(self.actuatedDofs)):
            self.actuatedDofs[i] += 6

        #40 dof upper body
        self.action_scale = np.ones(len(self.actuatedDofs))
        if not SPDActionSpace:
            self.action_scale *= 20
            if 6 in self.actuatedDofs:
                self.action_scale[self.actuatedDofs.tolist().index(6)] = 100
            if 7 in self.actuatedDofs:
                self.action_scale[self.actuatedDofs.tolist().index(7)] = 100
            self.action_scale[28-6:] *= 4#2.5 #20 -> 50

        if self.recurrency > 0:
            self.action_scale = np.concatenate([self.action_scale, np.ones(self.recurrency)])

        self.control_bounds = np.array([np.ones(len(self.actuatedDofs)+self.recurrency), np.ones(len(self.actuatedDofs)+self.recurrency)*-1])

        self.reset_number = 0
        self.numSteps = 0

        #create cloth scene
        clothScene = None

        if clothMeshStateFile is not None:
            clothScene = pyphysx.ClothScene(step=0.01,
                                            mesh_path=self.prefix + "/assets/" + clothMeshFile,
                                            state_path=self.prefix + "/../../../../" + clothMeshStateFile,
                                            scale=clothScale)
        else:
            clothScene = pyphysx.ClothScene(step=0.01,
                                            mesh_path=self.prefix + "/assets/" + clothMeshFile,
                                            scale=clothScale)

        clothScene.togglePinned(0, 0)  # turn off auto-pin

        self.separatedMesh = None
        if simulateCloth:
            self.separatedMesh = meshgraph.MeshGraph(clothscene=clothScene)

        self.reward = 0
        self.cumulativeReward = 0
        self.deformation = 0

        self.obs_size = obs_size
        skelFile = 'FullBodyCapsules_datadriven.skel'

        if self.locked_foot:
            skelFile = 'FullBodyCapsules_datadriven_lockedfoot.skel'

        #intialize the parent env
        if self.useOpenGL is True:
            DartClothEnv.__init__(self, cloth_scene=clothScene, model_paths=skelFile, frame_skip=2,
                                  observation_size=obs_size, action_bounds=self.control_bounds, screen_width=self.screenSize[0], screen_height=self.screenSize[1])
        else:
            DartClothEnv.__init__(self, cloth_scene=clothScene, model_paths=skelFile, frame_skip=2,
                                  observation_size=obs_size, action_bounds=self.control_bounds , disableViewer = True, visualize = False)

        #rescaling actions for SPD
        if SPDActionSpace:
            for ix, dof in enumerate(self.robot_skeleton.dofs):
                if dof.has_position_limit():
                    self.action_scale[ix] = 1.0
                    self.control_bounds[0][ix] = dof.position_upper_limit()
                    self.control_bounds[1][ix] = dof.position_lower_limit()
                    print("ix: " + str(ix) + " | control_bounds["+str(ix)+"]: " + str(self.control_bounds[0][ix]) + ", " + str(str(self.control_bounds[0][ix])))
                else:
                    self.action_scale[ix] = 3.14
                    self.control_bounds[0][ix] = 1.0
                    self.control_bounds[1][ix] = -1.0
            self.action_space = spaces.Box(self.control_bounds[1], self.control_bounds[0])

        print("action_space: " + str(self.action_space))
        print("action_scale: " + str(self.action_scale))
        print("control_bounds: " + str(self.control_bounds))

        #setup data-driven joint limits
        if self.dataDrivenJointLimts:
            #arms
            leftarmConstraint = pydart.constraints.HumanArmJointLimitConstraint(self.robot_skeleton.joint('j_bicep_left'), self.robot_skeleton.joint('elbowjL'), True)
            rightarmConstraint = pydart.constraints.HumanArmJointLimitConstraint(self.robot_skeleton.joint('j_bicep_right'), self.robot_skeleton.joint('elbowjR'), False)
            leftarmConstraint.add_to_world(self.dart_world)
            rightarmConstraint.add_to_world(self.dart_world)
            #legs #TODO: fix arm/leg side label?
            leftlegConstraint = pydart.constraints.HumanLegJointLimitConstraint(self.robot_skeleton.joint('j_thigh_left'), self.robot_skeleton.joint('j_shin_left'), self.robot_skeleton.joint('j_heel_left'), False)
            rightlegConstraint = pydart.constraints.HumanLegJointLimitConstraint(self.robot_skeleton.joint('j_thigh_right'), self.robot_skeleton.joint('j_shin_right'), self.robot_skeleton.joint('j_heel_right'), True)
            leftlegConstraint.add_to_world(self.dart_world)
            rightlegConstraint.add_to_world(self.dart_world)

        utils.EzPickle.__init__(self)

        if not self.gravity:
            self.dart_world.set_gravity(np.zeros(3))
        else:
            self.dart_world.set_gravity(np.array([0., -9.8, 0]))

        self.clothScene.setFriction(0, 0.5) #reset this anytime as desired

        self.collisionCapsuleInfo = None #set in updateClothCollisionStructures(capsules=True)
        self.collisionSphereInfo = None #set in updateClothCollisionStructures()
        self.updateClothCollisionStructures(capsules=True, hapticSensors=True)
        
        self.simulateCloth = simulateCloth

        #enable DART collision testing
        self.robot_skeleton.set_self_collision_check(True)
        self.robot_skeleton.set_adjacent_body_check(False)

        #setup collision filtering
        collision_filter = self.dart_world.create_collision_filter()
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[10],
                                           self.robot_skeleton.bodynodes[12])  # left forearm to fingers
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[5],
                                           self.robot_skeleton.bodynodes[7])  # right forearm to fingers
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[13])  # torso to neck
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[14])  # torso to head
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[3])  # torso to right shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[8])  # torso to left shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[13],
                                           self.robot_skeleton.bodynodes[3])  # neck to right shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[13],
                                           self.robot_skeleton.bodynodes[8])  # neck to left shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[14],
                                           self.robot_skeleton.bodynodes[3])  # head to right shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[14],
                                           self.robot_skeleton.bodynodes[8])  # head to left shoulder
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[3],
                                           self.robot_skeleton.bodynodes[8])  # right shoulder to left shoulder
        #TODO: disable collisions between lower body interfering nodes
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[15])  # torso to upper left leg
        collision_filter.add_to_black_list(self.robot_skeleton.bodynodes[1],
                                           self.robot_skeleton.bodynodes[18])  # torso to upper left leg


        for i in range(len(self.robot_skeleton.bodynodes)):
            print(self.robot_skeleton.bodynodes[i])
            
        for i in range(len(self.robot_skeleton.dofs)):
            if(i > 5):
                self.robot_skeleton.dofs[i].set_damping_coefficient(3.0)
                #self.robot_skeleton.dofs[i].set_spring_stiffness(50.0)d
            print(self.robot_skeleton.dofs[i])
            print("     damping:" + str(self.robot_skeleton.dofs[i].damping_coefficient()))
            print("     stiffness:" + str(self.robot_skeleton.dofs[i].spring_stiffness()))

        #enable joint limits
        for i in range(len(self.robot_skeleton.joints)):
            print(self.robot_skeleton.joints[i])

        #DART does not automatically limit joints with any unlimited dofs
        self.robot_skeleton.joints[4].set_position_limit_enforced(True)
        self.robot_skeleton.joints[9].set_position_limit_enforced(True)

        self.clothScene.setSelfCollisionDistance(distance=0.03)
        self.clothScene.step()
        self.clothScene.reset()

    def _getFile(self):
        return __file__

    def saveObjState(self, filename=None):
        print("Trying to save the object state")
        print("filename: " + str(filename))
        if filename is None:
            filename = "objState"
        self.clothScene.saveObjState(filename, 0)

    def saveCharacterState(self, filename=None):
        print("saving character state")
        if filename is None:
            filename = "characterState"
        print("filename " + str(filename))
        f = open(filename, 'w')
        for ix,dof in enumerate(self.robot_skeleton.q):
            if ix > 0:
                f.write(" ")
            f.write(str(dof))

        f.write("\n")

        for ix,dof in enumerate(self.robot_skeleton.dq):
            if ix > 0:
                f.write(" ")
            f.write(str(dof))
        f.close()

    def loadCharacterState(self, filename=None):
        openFile = "characterState"
        if filename is not None:
            openFile = filename
        f = open(openFile, 'r')
        qpos = np.zeros(self.robot_skeleton.ndofs)
        qvel = np.zeros(self.robot_skeleton.ndofs)
        for ix, line in enumerate(f):
            if ix > 1: #only want the first 2 file lines
                break
            words = line.split()
            if(len(words) != self.robot_skeleton.ndofs):
                break
            if(ix == 0): #position
                qpos = np.zeros(self.robot_skeleton.ndofs)
                for ixw, w in enumerate(words):
                    qpos[ixw] = float(w)
            else: #velocity (if available)
                qvel = np.zeros(self.robot_skeleton.ndofs)
                for ixw, w in enumerate(words):
                    qvel[ixw] = float(w)

        self.robot_skeleton.set_positions(qpos)
        self.robot_skeleton.set_velocities(qvel)
        f.close()

    def saveCharacterRenderState(self, filename=None):
        if filename is None:
            filename = "characterRenderState"
        #save the position and radius of each endpoint of each capsule for simple loading in Maya
        CSI = self.clothScene.getCollisionSpheresInfo()
        #info format: pos(3), radius, ignore(5)
        CCI = self.clothScene.getCollisionCapsuleInfo()
        #print("CSI: " + str(CSI))
        #print("CCI: " + str(CCI))

        f = open(filename, 'w')
        for capix in range(len(CCI)):
            for capix2 in range(len(CCI[capix])):
                if CCI[capix][capix2] != 0:
                    #this is a capsule
                    f.write(str(CSI[capix*9:capix*9+4]) + " " + str(CSI[capix2*9:capix2*9+4]) + "\n")
        f.close()

    def graphJointConstraintViolation(self, cix=1, pivotDof=8, otherDofs=[5,6,7], numPivotSamples=50, numOtherSamples=50, otherRange=(-1.0,1.0), counting=False):
        startTime = time.time()
        #print("graphJointConstraintViolation: collecting pivot samples")
        #collect samples:
        pivotLimits = [self.robot_skeleton.dofs[pivotDof].position_lower_limit(), self.robot_skeleton.dofs[pivotDof].position_upper_limit()]
        pivotSamples = []
        for i in range(numPivotSamples):
            pivotSamples.append(pivotLimits[0] + (pivotLimits[1]-pivotLimits[0])*(i/(numPivotSamples-1)))
        #print("graphJointConstraintViolation: collecting other samples")
        samples = []
        for dof in otherDofs:
            samples.append([])
            if self.robot_skeleton.dofs[dof].has_position_limit():
                #sample the whole range
                dofLimits = [self.robot_skeleton.dofs[dof].position_lower_limit(), self.robot_skeleton.dofs[dof].position_upper_limit()]
                for i in range(numOtherSamples):
                    samples[-1].append(dofLimits[0] + (dofLimits[1]-dofLimits[0])*(i/(numOtherSamples-1)))
            else:
                #sample otherRange about the current position
                currentPos = self.robot_skeleton.q[dof]
                for i in range(numOtherSamples):
                    samples[-1].append(otherRange[0]+currentPos + (otherRange[1]-otherRange[0])*(i/(numOtherSamples-1)))

        #print("graphJointConstraintViolation: checking violation")
        #check and store constraint violation at each sampled pose combination
        violations = [[] for i in range(len(otherDofs))]
        collisions = [[] for i in range(len(otherDofs))]
        positions = [[] for i in range(len(otherDofs))]
        currentPose = np.array(self.robot_skeleton.q)
        currentVel = np.array(self.robot_skeleton.dq)
        for ps in pivotSamples:
            for ix,list in enumerate(samples):
                violations[ix].append([])
                positions[ix].append([])
                collisions[ix].append([])
                for item in list:
                    qpos = np.array(currentPose)
                    qpos[pivotDof] = ps
                    qpos[otherDofs[ix]] = item
                    self.robot_skeleton.set_positions(qpos)
                    self.dart_world.step()
                    violations[ix][-1].append(self.dart_world.getAllConstraintViolations()[cix])
                    positions[ix][-1].append(item)
                    if self.dart_world.collision_result.num_contacts() > 0:
                        collisions[ix][-1].append(np.array([0.0, 1.0, 0.0, 0.2]))
                    else:
                        collisions[ix][-1].append(np.array([1.0, 0, 0, 0.0]))

        self.set_state(currentPose, currentVel)
        #print("graphJointConstraintViolation: done: " + str(violations))
        filenames = []
        for i in range(len(otherDofs)):
            ylabel = self.robot_skeleton.dofs[pivotDof].name
            xlabel = self.robot_skeleton.dofs[otherDofs[i]].name
            title = "Constraint violation " + xlabel + " vs. " + ylabel
            filename = xlabel + "_vs_" + ylabel
            points = [[currentPose[otherDofs[i]], currentPose[pivotDof]]]
            #print("about to create image " + filename)
            filenames.append(renderUtils.render2DHeatmap(data=violations[i], overlay=collisions[i], extent=[samples[i][0],samples[i][-1], pivotLimits[0],pivotLimits[1]], title=title, xlabel=xlabel, ylabel=ylabel, points=points, filename=filename))
            #print("...done")
        #print("finished creating the images")
        outfilename = ""
        if counting:
            outfilename = "../image_matrix_output/%05d" % self.numSteps
        renderUtils.imageMatrixFrom(filenames=filenames, outfilename=outfilename)
        #print("Done graphing constraints. Took " + str(time.time()-startTime) + " seconds.")

    def updateBeforeSimulation(self):
        #any pre-sim updates should happen here
        a=0

    def checkTermination(self, tau, s, obs):
        #check the termination conditions and return: done,reward
        if np.amax(np.absolute(s[:len(self.robot_skeleton.q)])) > 10:
            print("Detecting potential instability")
            print(s)
            return True, -500
        elif not np.isfinite(s).all():
            print("Infinite value detected..." + str(s))
            return True, -500

        return False, 0

    def computeReward(self, tau):
        #compute and return reward at the current state
        return 0

    def _step(self, a):
        #print("a: " + str(a))
        startTime = time.time()
        if self.reset_number < 1 or not self.simulating:
            return np.zeros(self.obs_size), 0, False, {}

        #save state for rendering
        if self.recordForRendering:
            fname = self.recordForRenderingOutputPrefix
            objfname_ix = fname + "%05d" % self.numSteps
            charfname_ix = fname + "_char%05d" % self.numSteps
            self.saveObjState(filename=objfname_ix)
            self.saveCharacterRenderState(filename=charfname_ix)

        self.stateTraj.append(self.robot_skeleton.q)

        #try:
        if self.graphViolation:
            if self.numSteps%self.violationGraphFrequency == 0:
                self.graphJointConstraintViolation(counting=True)

        if self.graphClothViolation:
            clothViolation = self.clothScene.getNumSelfCollisions()
            maxDef, minDef, avgDef, variance, ratios = self.clothScene.getAllDeformationStats(cid=0)
            defPenalty = (math.tanh(0.14 * (maxDef - 25)) + 1) / 2.0 #taken from reward in envs
            #print(maxDef)
            #print(avgDef)
            self.clothViolationGraph.addToLinePlot(data=[clothViolation/5.0, maxDef, defPenalty*10])

        startTime2 = time.time()
        self.additionalAction = np.zeros(len(self.additionalAction))
        #self.additionalAction += np.random.uniform(-1, 1, len(self.additionalAction))
        #self.additionalAction should be set in updateBeforeSimulation
        self.updateBeforeSimulation()  # any env specific updates before simulation
        # print("updateBeforeSimulation took " + str(time.time() - startTime2))
        try:
            self.avgtimings["updateBeforeSimulation"] += time.time() - startTime2
        except:
            self.avgtimings["updateBeforeSimulation"] = time.time() - startTime2

        full_control = np.array(a)

        if self.SPDTarget is not None and self.SPD is not None:
            self.SPD.target = np.array(self.SPDTarget)
            self.additionalAction += self.SPD.query()
            #print(self.additionalAction)
        else:
            full_control[:len(self.additionalAction)] = full_control[:len(self.additionalAction)] + self.additionalAction
        #print("full_control = " + str(full_control))
        clamped_control = np.array(full_control)
        for i in range(len(clamped_control)):
            if clamped_control[i] > self.control_bounds[0][i]:
                clamped_control[i] = self.control_bounds[0][i]
            if clamped_control[i] < self.control_bounds[1][i]:
                clamped_control[i] = self.control_bounds[1][i]
        #print("clamped_control = " + str(clamped_control))

        tau = np.multiply(clamped_control, self.action_scale)

        #apply action and simulate
        if len(tau) < len(self.robot_skeleton.q):
            newtau = np.array(tau)
            tau = np.zeros(len(self.robot_skeleton.q))
            for ix,dof in enumerate(self.actuatedDofs):
                tau[dof] = newtau[ix]

        self.actionTrajectory.append(tau)

        startTime2 = time.time()
        if self.SPDTarget is not None and self.SPD is not None:
            self.do_simulation(self.additionalAction, self.frame_skip)
        else:
            self.do_simulation(tau[:len(self.robot_skeleton.q)], self.frame_skip)

        try:
            self.avgtimings["do_simulation"] += time.time() - startTime2
        except:
            self.avgtimings["do_simulation"] = time.time() - startTime2


        startTime2 = time.time()
        reward = self.computeReward(tau=tau)
        #print("computeReward took " + str(time.time() - startTime2))
        try:
            self.avgtimings["computeReward"] += time.time() - startTime2
        except:
            self.avgtimings["computeReward"] = time.time() - startTime2


        startTime2 = time.time()
        ob = self._get_obs()
        s = self.state_vector()
        #print("obs and state took " + str(time.time() - startTime2))
        try:
            self.avgtimings["obs"] += time.time() - startTime2
        except:
            self.avgtimings["obs"] = time.time() - startTime2


        #update physx capsules
        self.updateClothCollisionStructures(hapticSensors=True)

        done, terminationReward = self.checkTermination(tau, s, ob)
        reward += terminationReward
        self.reward = reward
        self.cumulativeReward += self.reward
        self.rewardTrajectory.append(self.reward)

        self.numSteps += 1
        #print("_step took " + str(time.time() - startTime))
        try:
            self.avgtimings["_step"] += time.time() - startTime2
        except:
            self.avgtimings["_step"] = time.time() - startTime2

        '''
        if(done):
            print("finishing step while done...")
            print(ob)
            print(self.reward)
            print(done)
        '''

        self.updateActionGraph()

        return ob, self.reward, done, {}

    def _get_obs(self):
        print("base observation")
        return np.zeros(self.obs_size)

    def additionalResets(self):
        #do any additional reseting here
        a=0

    def reset_model(self):
        #print("starting reset " + str(self.reset_number))
        if self.reset_number == 0:
            self.setSeed = random.random()
        #print(str(self.setSeed) + " example: " + str(random.random()))
        #print()
        self.rewardsData.reset()
        self.stateTraj = []
        self.actionTrajectory = []
        self.changedFocus = True

        #---------------------------
        #random seeding

        seeds=[]
        #difficultySeeds = []
        #seeds = seeds+difficultySeeds
        #seed = self.reset_number
        #print(seeds)
        try:
            seed = seeds[self.reset_number]
        except:
            seed = self.reset_number
            #print("all given seeds simulated")
        #seed = 8
        #print("rollout: " + str(self.reset_number+1) +", seed: " + str(seed))
        #random.seed(seed)
        #self.np_random.seed(seed)
        #np.random.seed(seed)
        #self.clothScene.seedRandom(seed) #unecessary

        #print("random.random(): " + str(random.random()))
        #print("np.random.random: " + str(np.random.random()))
        #print("self.np_random.random: " + str(self.np_random.uniform() ))

        #done random seeding
        #----------------------------------

        if self.graphClothViolation:
            if self.saveClothViolationGraphOnReset:
                self.clothViolationGraph.save(filename="clothViolationGraphRS"+str(self.reset_number))
            self.clothViolationGraph.close()
            self.clothViolationGraph = pyutils.LineGrapher(title="Cloth Violation", numPlots=3)

        self.rewardTrajectory = []
        startTime = time.time()
        #try:
        #print("reset")
        self.cumulativeReward = 0
        self.dart_world.reset()
        if self.simulateCloth:
            self.clothScene.setSelfCollisionDistance(0.03)
            self.clothScene.reset()
        #self.clothScene.setFriction(0, 0.4)

        self.additionalResets()

        #update physx capsules
        self.updateClothCollisionStructures(hapticSensors=True)
        if self.simulateCloth:
            self.clothScene.clearInterpolation()

        self.avgtimings = {}
        self.reset_number += 1
        self.numSteps = 0

        #print("done reset " + str(self.reset_number-1))

        return self._get_obs()

    def updateClothCollisionStructures(self, capsules=False, hapticSensors=False):
        #TODO: need to overhaul this for the lower body
        a=0
        #collision spheres creation
        fingertip = np.array([0.0, -0.06, 0.0])
        z = np.array([0.,0,0])
        #upper body
        cs0 = self.robot_skeleton.bodynodes[1].to_world(z)
        cs1 = self.robot_skeleton.bodynodes[2].to_world(z)
        cs2 = self.robot_skeleton.bodynodes[14].to_world(z)
        cs3 = self.robot_skeleton.bodynodes[14].to_world(np.array([0,0.175,0]))
        cs4 = self.robot_skeleton.bodynodes[4].to_world(z)
        cs5 = self.robot_skeleton.bodynodes[5].to_world(z)
        cs6 = self.robot_skeleton.bodynodes[6].to_world(z)
        cs7 = self.robot_skeleton.bodynodes[7].to_world(z)
        cs8 = self.robot_skeleton.bodynodes[7].to_world(fingertip)
        cs9 = self.robot_skeleton.bodynodes[9].to_world(z)
        cs10 = self.robot_skeleton.bodynodes[10].to_world(z)
        cs11 = self.robot_skeleton.bodynodes[11].to_world(z)
        cs12 = self.robot_skeleton.bodynodes[12].to_world(z)
        cs13 = self.robot_skeleton.bodynodes[12].to_world(fingertip)
        csVars0 = np.array([0.15, -1, -1, 0,0,0])
        csVars1 = np.array([0.07, -1, -1, 0,0,0])
        csVars2 = np.array([0.1, -1, -1, 0,0,0])
        csVars3 = np.array([0.1, -1, -1, 0,0,0])
        csVars4 = np.array([0.065, -1, -1, 0,0,0])
        csVars5 = np.array([0.05, -1, -1, 0,0,0])
        csVars6 = np.array([0.0365, -1, -1, 0,0,0])
        csVars7 = np.array([0.04, -1, -1, 0,0,0])
        csVars8 = np.array([0.046, -1, -1, 0,0,0])
        csVars9 = np.array([0.065, -1, -1, 0,0,0])
        csVars10 = np.array([0.05, -1, -1, 0,0,0])
        csVars11 = np.array([0.0365, -1, -1, 0,0,0])
        csVars12 = np.array([0.04, -1, -1, 0,0,0])
        csVars13 = np.array([0.046, -1, -1, 0,0,0])

        #lower body: (character's)left leg
        cs14 = self.robot_skeleton.bodynodes[15].to_world(z)  # l-upperleg
        csVars14 = np.array([0.077, -1, -1, 0, 0, 0])
        cs15 = self.robot_skeleton.bodynodes[16].to_world(z)  # l-lowerleg
        csVars15 = np.array([0.065, -1, -1, 0, 0, 0])
        cs16 = self.robot_skeleton.bodynodes[17].to_world(z)  # l-foot_center
        csVars16 = np.array([0.0525, -1, -1, 0, 0, 0])
        cs17 = self.robot_skeleton.bodynodes[17].to_world(np.array([-0.025, 0, 0.03]))  # l-foot_l-heel
        csVars17 = np.array([0.05775, -1, -1, 0, 0, 0])
        cs18 = self.robot_skeleton.bodynodes[17].to_world(np.array([0.025, 0, 0.03]))  # l-foot_r-heel
        csVars18 = np.array([0.05775, -1, -1, 0, 0, 0])
        cs19 = self.robot_skeleton.bodynodes[17].to_world(np.array([0, 0, -0.15]))  # l-foot_toe
        csVars19 = np.array([0.0525, -1, -1, 0, 0, 0])

        # lower body: (character's)right leg
        cs20 = self.robot_skeleton.bodynodes[18].to_world(z)  # r-upperleg
        csVars20 = np.array([0.077, -1, -1, 0, 0, 0])
        cs21 = self.robot_skeleton.bodynodes[19].to_world(z)  # r-lowerleg
        csVars21 = np.array([0.065, -1, -1, 0, 0, 0])
        cs22 = self.robot_skeleton.bodynodes[20].to_world(z)  # r-foot_center
        csVars22 = np.array([0.0525, -1, -1, 0, 0, 0])
        cs23 = self.robot_skeleton.bodynodes[20].to_world(np.array([-0.025, 0, 0.03]))  # r-foot_l-heel
        csVars23 = np.array([0.05775, -1, -1, 0, 0, 0])
        cs24 = self.robot_skeleton.bodynodes[20].to_world(np.array([0.025, 0, 0.03]))  # r-foot_r-heel
        csVars24 = np.array([0.05775, -1, -1, 0, 0, 0])
        cs25 = self.robot_skeleton.bodynodes[20].to_world(np.array([0, 0, -0.15]))  # r-foot_toe
        csVars25 = np.array([0.0525, -1, -1, 0, 0, 0])


        collisionSpheresInfo = np.concatenate([cs0, csVars0, cs1, csVars1, cs2, csVars2, cs3, csVars3, cs4, csVars4, cs5, csVars5, cs6, csVars6, cs7, csVars7, cs8, csVars8, cs9, csVars9, cs10, csVars10, cs11, csVars11, cs12, csVars12, cs13, csVars13, cs14, csVars14, cs15, csVars15, cs16, csVars16, cs17, csVars17, cs18, csVars18, cs19, csVars19, cs20, csVars20, cs21, csVars21, cs22, csVars22, cs23, csVars23, cs24, csVars24, cs25, csVars25]).ravel()

        #inflate collision objects
        #for i in range(int(len(collisionSpheresInfo)/9)):
        #    collisionSpheresInfo[i*9 + 3] *= 1.15

        self.collisionSphereInfo = np.array(collisionSpheresInfo)
        #collisionSpheresInfo = np.concatenate([cs0, csVars0, cs1, csVars1]).ravel()
        if np.isnan(np.sum(collisionSpheresInfo)): #this will keep nans from propagating into PhysX resulting in segfault on reset()
            return
        self.clothScene.setCollisionSpheresInfo(collisionSpheresInfo)
        
        if capsules is True:
            #collision capsules creation
            collisionCapsuleInfo = np.zeros((26,26))
            #upper body
            collisionCapsuleInfo[0,1] = 1
            collisionCapsuleInfo[1,2] = 1
            collisionCapsuleInfo[1,4] = 1
            collisionCapsuleInfo[1,9] = 1
            collisionCapsuleInfo[2,3] = 1
            collisionCapsuleInfo[4,5] = 1
            collisionCapsuleInfo[5,6] = 1
            collisionCapsuleInfo[6,7] = 1
            collisionCapsuleInfo[7,8] = 1
            collisionCapsuleInfo[9,10] = 1
            collisionCapsuleInfo[10,11] = 1
            collisionCapsuleInfo[11,12] = 1
            collisionCapsuleInfo[12,13] = 1
            #lower body
            collisionCapsuleInfo[14,15] = 1
            collisionCapsuleInfo[15,16] = 1
            collisionCapsuleInfo[17,19] = 1
            collisionCapsuleInfo[18,19] = 1

            collisionCapsuleInfo[20, 21] = 1
            collisionCapsuleInfo[21, 22] = 1
            collisionCapsuleInfo[23, 25] = 1
            collisionCapsuleInfo[24, 25] = 1

            collisionCapsuleBodynodes = -1 * np.ones((26,26))
            #upper body
            collisionCapsuleBodynodes[0, 1] = 1
            collisionCapsuleBodynodes[1, 2] = 13
            collisionCapsuleBodynodes[1, 4] = 3
            collisionCapsuleBodynodes[1, 9] = 8
            collisionCapsuleBodynodes[2, 3] = 14
            collisionCapsuleBodynodes[4, 5] = 4
            collisionCapsuleBodynodes[5, 6] = 5
            collisionCapsuleBodynodes[6, 7] = 6
            collisionCapsuleBodynodes[7, 8] = 7
            collisionCapsuleBodynodes[9, 10] = 9
            collisionCapsuleBodynodes[10, 11] = 10
            collisionCapsuleBodynodes[11, 12] = 11
            collisionCapsuleBodynodes[12, 13] = 12
            #lower body
            collisionCapsuleInfo[14, 15] = 15
            collisionCapsuleInfo[15, 16] = 16
            collisionCapsuleInfo[17, 19] = 17
            collisionCapsuleInfo[18, 19] = 17

            collisionCapsuleInfo[20, 21] = 18
            collisionCapsuleInfo[21, 22] = 19
            collisionCapsuleInfo[23, 25] = 20
            collisionCapsuleInfo[24, 25] = 20

            self.clothScene.setCollisionCapsuleInfo(collisionCapsuleInfo, collisionCapsuleBodynodes)
            self.collisionCapsuleInfo = np.array(collisionCapsuleInfo)
            
        if hapticSensors is True:
            #hapticSensorLocations = np.concatenate([cs0, LERP(cs0, cs1, 0.33), LERP(cs0, cs1, 0.66), cs1, LERP(cs1, cs2, 0.33), LERP(cs1, cs2, 0.66), cs2, LERP(cs2, cs3, 0.33), LERP(cs2, cs3, 0.66), cs3])
            #hapticSensorLocations = np.concatenate([cs0, LERP(cs0, cs1, 0.25), LERP(cs0, cs1, 0.5), LERP(cs0, cs1, 0.75), cs1, LERP(cs1, cs2, 0.25), LERP(cs1, cs2, 0.5), LERP(cs1, cs2, 0.75), cs2, LERP(cs2, cs3, 0.25), LERP(cs2, cs3, 0.5), LERP(cs2, cs3, 0.75), cs3])

            #upper body sensors
            hapticSensorLocations_upper = np.concatenate([cs0, cs1, cs2, cs3, cs4, LERP(cs4, cs5, 0.33), LERP(cs4, cs5, 0.66), cs5, LERP(cs5, cs6, 0.33), LERP(cs5,cs6,0.66), cs6, cs7, cs8, cs9, LERP(cs9, cs10, 0.33), LERP(cs9, cs10, 0.66), cs10, LERP(cs10, cs11, 0.33), LERP(cs10, cs11, 0.66), cs11, cs12, cs13])
            hapticSensorRadii_upper = np.array([csVars0[0], csVars1[0], csVars2[0], csVars3[0], csVars4[0], LERP(csVars4[0], csVars5[0], 0.33), LERP(csVars4[0], csVars5[0], 0.66), csVars5[0], LERP(csVars5[0], csVars6[0], 0.33), LERP(csVars5[0], csVars6[0], 0.66), csVars6[0], csVars7[0], csVars8[0], csVars9[0], LERP(csVars9[0], csVars10[0], 0.33), LERP(csVars9[0], csVars10[0], 0.66), csVars10[0], LERP(csVars10[0], csVars11[0], 0.33), LERP(csVars10[0], csVars11[0], 0.66), csVars11[0], csVars12[0], csVars13[0]])

            #lower body sensors
            hapticSensorLocations_lower = np.concatenate([cs14, LERP(cs14, cs15, 0.33), LERP(cs14, cs15, 0.66), cs15, LERP(cs15, cs16, 0.33), LERP(cs15, cs16, 0.66), cs17, cs18, cs19, cs20, LERP(cs20, cs21, 0.33), LERP(cs20, cs21, 0.66), cs21, LERP(cs21, cs22, 0.33), LERP(cs21, cs22, 0.66), cs23, cs24, cs25])
            hapticSensorRadii_lower = np.array([csVars14[0], LERP(csVars14[0], csVars15[0], 0.33), LERP(csVars14[0], csVars15[0], 0.66), csVars15[0], LERP(csVars15[0], csVars16[0], 0.33), LERP(csVars15[0], csVars16[0], 0.66), csVars17[0], csVars18[0], csVars19[0], csVars20[0], LERP(csVars20[0], csVars21[0], 0.33), LERP(csVars20[0], csVars21[0], 0.66), csVars21[0], LERP(csVars21[0], csVars22[0], 0.33), LERP(csVars21[0], csVars22[0], 0.66), csVars23[0], csVars24[0], csVars25[0]])

            #combine upper and lower
            hapticSensorLocations = np.concatenate([hapticSensorLocations_upper, hapticSensorLocations_lower])
            hapticSensorRadii = np.concatenate([hapticSensorRadii_upper, hapticSensorRadii_lower])

            self.clothScene.setHapticSensorLocations(hapticSensorLocations)
            self.clothScene.setHapticSensorRadii(hapticSensorRadii)

    def getViewer(self, sim, title=None, extraRenderFunc=None, inputFunc=None, resetFunc=None):
        return DartClothEnv.getViewer(self, sim, title, self.extraRenderFunction, self.inputFunc, self.reset_model)

    def inputFunc(self, repeat=False):
        pyutils.inputGenie(domain=self, repeat=repeat)

    def extraRenderFunction(self):
        '''This function is overwritten by child classes'''
        renderUtils.setColor(color=[0.0, 0.0, 0])
        GL.glBegin(GL.GL_LINES)
        GL.glVertex3d(0,0,0)
        GL.glVertex3d(-1,0,0)
        GL.glEnd()

        renderUtils.setColor([0,0,0])
        renderUtils.drawLineStrip(points=[self.robot_skeleton.bodynodes[4].to_world(np.array([0.0,0,-0.075])), self.robot_skeleton.bodynodes[4].to_world(np.array([0.0,-0.3,-0.075]))])
        renderUtils.drawLineStrip(points=[self.robot_skeleton.bodynodes[9].to_world(np.array([0.0,0,-0.075])), self.robot_skeleton.bodynodes[9].to_world(np.array([0.0,-0.3,-0.075]))])

        textHeight = 15
        textLines = 2

        # SPD pose rendering
        if self.SPDTarget is not None:
            links = pyutils.getRobotLinks(self.robot_skeleton, pose=self.SPDTarget)
            renderUtils.drawLines(lines=links)

        if self.renderUI:
            renderUtils.setColor(color=[0.,0,0])
            if self.totalTime > 0:
                self.clothScene.drawText(x=15., y=textLines*textHeight, text="Steps = " + str(self.numSteps) + " framerate = " + str(self.numSteps/self.totalTime), color=(0., 0, 0))
                textLines += 1
            self.clothScene.drawText(x=15., y=textLines*textHeight, text="Reward = " + str(self.reward), color=(0., 0, 0))
            textLines += 1
            self.clothScene.drawText(x=15., y=textLines * textHeight, text="Cumulative Reward = " + str(self.cumulativeReward), color=(0., 0, 0))
            textLines += 1
            if self.numSteps > 0:
                renderUtils.renderDofs(robot=self.robot_skeleton, restPose=None, renderRestPose=False)

    def viewer_setup(self):
        if self._get_viewer().scene is not None:
            self._get_viewer().scene.tb.trans[2] = -3.5
            self._get_viewer().scene.tb._set_theta(180)
            self._get_viewer().scene.tb._set_phi(180)
        self.track_skeleton_id = 0
        if not self.renderDARTWorld:
            self.viewer.renderWorld = False
        self.clothScene.renderCollisionCaps = True
        self.clothScene.renderCollisionSpheres = True

    def computeZMP(self):
        #return 2D ZMP in xz plane
        # scalar ZMP computation:
        xZMP = 0
        zZMP = 0
        g = -self.dart_world.gravity()[1]
        denom = self.robot_skeleton.mass() * g  # mg
        for node in self.robot_skeleton.bodynodes:
            m = node.mass()
            G = node.com()
            H = node.com_angular_momentum()
            Gdd = node.com_linear_acceleration()
            xZMP += m * G[0] * g - (H[2] + m * (G[1] * Gdd[0] - G[0] * Gdd[1]))
            zZMP += m * G[2] * g - (H[0] + m * (G[2] * Gdd[1] - G[1] * Gdd[2]))

            denom += m * Gdd[1]

        xZMP /= denom
        zZMP /= denom
        return np.array([xZMP, zZMP])

    def updateActionGraph(self):
        if self.actionGraph is not None:
            if self.changedFocus:
                self.actionGraph.close()
                self.actionGraph = None
                self.changedFocus = False
                self.lastUpdate = 0

        if self.graphingActions:
            if self.actionGraph is None:
                if self.lastUpdate > self.updateDelay:
                    self.lastUpdate = 0
                    self.changedFocus = False
                    self.actionGraph = pyutils.LineGrapher(title="Action Graph", legend=True)

                    #for each focus, fill the ydata
                    yData = []
                    self.actionGraph.xdata = np.arange(len(self.actionTrajectory)).tolist()
                    if(len(self.actionTrajectory) > 0):
                        for gix,fix in enumerate(self.actionGraphFoci):
                            yData.append([])
                            for i in range(len(self.actionTrajectory)):
                                yData[gix].append(self.actionTrajectory[i][fix])
                            self.actionGraph.plotData(ydata=yData[gix], label=str(fix))
            else:
                #adding a new data entry
                newData = []
                for gix, fix in enumerate(self.actionGraphFoci):
                    newData.append(self.actionTrajectory[-1][fix])
                self.actionGraph.addToLinePlot(newData)

        self.lastUpdate += 1