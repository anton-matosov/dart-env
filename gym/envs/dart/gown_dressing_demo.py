# This environment is created by Alexander Clegg (alexanderwclegg@gmail.com)

import numpy as np
import quaternion
from gym import utils
from gym.envs.dart.dart_cloth_env import *
import random
import time

from pyPhysX.colors import *
import pyPhysX.pyutils as pyutils
from pyPhysX.clothHandles import *
from pyPhysX.clothfeature import *

import OpenGL.GL as GL
import OpenGL.GLU as GLU
import OpenGL.GLUT as GLUT

''' This env is setup for upper body interaction with gown garment gripped and moved on target path'''

class DartClothGownDemoEnv(DartClothEnv, utils.EzPickle):
    def __init__(self):
        self.target = np.array([0.8, -0.6, 0.6])
        self.targetInObs = True
        self.arm = 2

        #22 dof upper body
        self.action_scale = np.ones(22)*10
        self.control_bounds = np.array([np.ones(22), np.ones(22)*-1])

        if self.arm > 0:
            self.action_scale = np.ones(11) * 10
            self.control_bounds = np.array([np.ones(11), np.ones(11) * -1])

        '''self.action_scale[0] = 150  # torso
        self.action_scale[1] = 150
        self.action_scale[2] = 100  # spine
        self.action_scale[3] = 50  # clav
        self.action_scale[4] = 50
        self.action_scale[5] = 30  # shoulder
        self.action_scale[6] = 30
        self.action_scale[7] = 20
        self.action_scale[8] = 20  # elbow
        self.action_scale[9] = 8  # wrist
        self.action_scale[10] = 8'''

        self.numSteps = 0 #increments every step, 0 on reset

        self.arm_progress = 0.  # set in step when first queried
        self.armLength = -1.0  # set when arm progress is queried

        # handle node setup
        self.handleNode = None
        self.gripper = None

        #interactive handle mode
        self.interactiveHandleNode = False

        #randomized spline target mode
        self.randomHandleTargetSpline = False
        self.handleTargetSplineWindow = 10.0 #time window for the full motion (split into equal intervals b/t CPs)
        self.numHandleTargetSplinePoints = 4
        self.handleTargetSplineGlobalBounds = [np.array([0.75,0.3,1.0]), np.array([-0.0,-0.5,0.])] #total drift allowed from origin for target orgs
        self.handleTargetSplineLocalBounds = [np.array([0.25,0.25,0.35]), np.array([-0.25,-0.25,-0.05])] #cartesian drift allowed b/t neighboring CPs
        #TODO: add rotation
        #self.handleTargetSplineGlobalRotationBounds

        #linear spline target mode
        self.randomHandleTargetLinear = True
        self.handleTargetLinearWindow = 10.0
        self.handleTargetLinearInitialRange = pyutils.BoxFrame(c0=np.array([0.7,0.5,0.15]),
                                                               c1=np.array([-0.3, -0.5, -0.15]),
                                                               org=np.array([-0.17205264,  0.12056234, -1.07377446]))
        self.handleTargetLinearEndRange = pyutils.BoxFrame(c0=np.array([0.5, 0.3, 0.2]),
                                                           c1=np.array([0.1, -0.1, -0.1]),
                                                           org=np.array([0.,0.,0.]))

        #debugging boxes for visualizing distributions
        self.drawDebuggingBoxes = True
        self.debuggingBoxes = [self.handleTargetLinearInitialRange, self.handleTargetLinearEndRange]
        self.debuggingColors = [[0., 1, 0], [0, 0, 1.], [1., 0, 0], [1., 1., 0], [1., 0., 1.], [0, 1., 1.]]

        #create cloth scene
        clothScene = pyphysx.ClothScene(step=0.01,
                                        mesh_path="/home/aclegg3/Documents/dev/dart-env/gym/envs/dart/assets/fullgown1.obj",
                                        #mesh_path="/home/alexander/Documents/dev/dart-env/gym/envs/dart/assets/tshirt_m.obj",
                                        #state_path="/home/alexander/Documents/dev/1stSleeveState.obj",
                                        scale=1.3)

        clothScene.togglePinned(0,0) #turn off auto-pin
        #clothScene.togglePinned(0, 144)
        #clothScene.togglePinned(0, 190)

        self.CP0Feature = ClothFeature(verts=[475, 860, 1620, 1839, 994, 469, 153, 531, 1932, 140],
                                       clothScene=clothScene)
        self.armLength = -1.0  # set when arm progress is queried

        observation_size = 66 + 66  # pose(sin,cos), pose vel, haptics
        if self.targetInObs:
            observation_size += 6  # target reaching

        #intialize the parent env
        DartClothEnv.__init__(self, cloth_scene=clothScene, model_paths='UpperBodyCapsules.skel', frame_skip=4,
                              observation_size=observation_size, action_bounds=self.control_bounds, disableViewer=True, visualize=False)
        utils.EzPickle.__init__(self)

        #setup HandleNode here
        self.handleNode = HandleNode(self.clothScene, org=np.array([0.05,0.034,-0.975]))
        if self.interactiveHandleNode:
            self.viewer.interactors[2].frame.org = self.handleNode.org
            self.viewer.interactors[2].frame.orienation = self.handleNode.orientation
            self.viewer.interactors[2].frame.updateQuaternion()

        #self.handleNode.addVertex(0)
        #self.handleNode.addVertices(verts=[1552, 2090, 1525, 954, 1800, 663, 1381, 1527, 1858, 1077, 759, 533, 1429, 1131])

        #self.gripper = pyutils.BoxFrame(c0=np.array([0.06, -0.075, 0.06]), c1=np.array([-0.06, -0.125, -0.06]))
        #self.gripper = pyutils.EllipsoidFrame(c0=np.array([0,-0.1,0]), dim=np.array([0.05,0.025,0.05]))
        #self.gripper.setTransform(self.robot_skeleton.bodynodes[8].T)
        
        self.clothScene.seedRandom(random.randint(1,1000))
        self.clothScene.setFriction(0, 0.4)
        
        self.updateClothCollisionStructures(capsules=True, hapticSensors=True)
        
        self.simulateCloth = True
        
        self.renderDofs = True #if true, show dofs text 
        self.renderForceText = False
        
        self.reset_number = 0 #increments on env.reset()

        '''
        for i in range(len(self.robot_skeleton.bodynodes)):
            print(self.robot_skeleton.bodynodes[i])

        for i in range(len(self.robot_skeleton.dofs)):
            print(self.robot_skeleton.dofs[i])
        '''

        print("done init")

    def limits(self, dof_ix):
        return np.array([self.robot_skeleton.dof(dof_ix).position_lower_limit(), self.robot_skeleton.dof(dof_ix).position_upper_limit()])

    def saveObjState(self):
        print("Trying to save the object state")
        self.clothScene.saveObjState("objState", 0)
        
    def loadObjState(self):
        self.clothScene.loadObjState("objState", 0)

    def _step(self, a):
        clamped_control = np.array(a)
        for i in range(len(clamped_control)):
            if clamped_control[i] > self.control_bounds[0][i]:
                clamped_control[i] = self.control_bounds[0][i]
            if clamped_control[i] < self.control_bounds[1][i]:
                clamped_control[i] = self.control_bounds[1][i]
        tau = np.multiply(clamped_control, self.action_scale)


        if self.handleNode is not None:
            #self.handleNode.setTranslation(T=self.viewer.interactors[2].frame.org)
            if self.interactiveHandleNode:
                self.handleNode.org = self.viewer.interactors[2].frame.org
                self.handleNode.setOrientation(R=self.viewer.interactors[2].frame.orientation)
            #self.handleNode.setTransform(self.robot_skeleton.bodynodes[8].T)
            self.handleNode.step()

        #if self.gripper is not None:
        #    self.gripper.setTransform(self.robot_skeleton.bodynodes[8].T)

        #increment self collision distance test
        #currentDistance = self.clothScene.getSelfCollisionDistance()
        #print("current self-collision distance = " + str(currentDistance))
        #self.clothScene.setSelfCollisionDistance(currentDistance + 0.0001)

        #apply action and simulate
        # apply action and simulate
        if self.arm == 1:
            tau = np.concatenate([tau, np.zeros(11)])
        elif self.arm == 2:
            tau = np.concatenate([tau[:3], np.zeros(8), tau[3:], np.zeros(3)])
        self.do_simulation(tau, self.frame_skip)

        self.target = pyutils.getVertCentroid(self.CP0Feature.verts, self.clothScene)
        self.dart_world.skeletons[0].q = [0, 0, 0, self.target[0], self.target[1], self.target[2]]

        self.CP0Feature.fitPlane()

        reward = 0
        self.arm_progress = self.armSleeveProgress() / self.armLength
        ob = self._get_obs()
        s = self.state_vector()

        reward += self.arm_progress
        
        #update physx capsules
        self.updateClothCollisionStructures(hapticSensors=True)
        
        #check termination conditions
        done = False

        clothDeformation = 0
        if self.simulateCloth is True:
            clothDeformation = self.clothScene.getMaxDeformationRatio(0)

        if not np.isfinite(s).all():
            #print("Infinite value detected..." + str(s))
            done = True
            reward -= 500
        elif (clothDeformation > 20):
            #print("Deformation Termination")
            done = True
            reward -= 5000
        elif self.armLength > 0 and self.arm_progress >= 0.95:
            done=True
            reward = 1000
            print("Dressing completed!")

        self.numSteps += 1

        return ob, reward, done, {}

    def _get_obs(self):
        '''get_obs'''
        f_size = 66
        theta = self.robot_skeleton.q

        if self.simulateCloth is True:
            f = self.clothScene.getHapticSensorObs()#get force from simulation
        else:
            f = np.zeros(f_size)

        obs = np.concatenate([np.cos(theta), np.sin(theta), self.robot_skeleton.dq]).ravel()

        if self.targetInObs:
            fingertip = np.array([0.0, -0.06, 0.0])
            vec = None
            if self.arm == 1:
                vec = self.robot_skeleton.bodynodes[8].to_world(fingertip) - self.target
            else:
                vec = self.robot_skeleton.bodynodes[14].to_world(fingertip) - self.target
            obs = np.concatenate([obs, vec, self.target]).ravel()

        obs = np.concatenate([obs, f * 3.]).ravel()
        #obs = np.concatenate([np.cos(theta), np.sin(theta), self.robot_skeleton.dq, vec, self.target, f]).ravel()
        #obs = np.concatenate([theta, self.robot_skeleton.dq, f]).ravel()
        return obs

    def reset_model(self):
        '''reset_model'''
        self.numSteps = 0
        self.dart_world.reset()
        self.clothScene.reset()
        qpos = self.robot_skeleton.q + self.np_random.uniform(low=-.015, high=.015, size=self.robot_skeleton.ndofs)
        qvel = self.robot_skeleton.dq + self.np_random.uniform(low=-.025, high=.025, size=self.robot_skeleton.ndofs)
        self.set_state(qpos, qvel)

        self.clothScene.rotateCloth(0, self.clothScene.getRotationMatrix(a=3.14, axis=np.array([0, 0, 1.])))
        self.clothScene.rotateCloth(0, self.clothScene.getRotationMatrix(a=3.14, axis=np.array([0, 1., 0.])))
        self.clothScene.translateCloth(0, np.array([0.75, -0.5, -0.5]))  # shirt in front of person
        #self.clothScene.rotateCloth(0, self.clothScene.getRotationMatrix(a=random.uniform(0, 6.28), axis=np.array([0,0,1.])))
        
        #load cloth state from ~/Documents/dev/objFile.obj
        #self.clothScene.loadObjState()

        #update physx capsules
        self.updateClothCollisionStructures(hapticSensors=True)

        self.handleNode.clearHandles()
        #self.handleNode.addVertex(vid=0)
        #self.clothScene.setPinned(cid=0, vid=0)

        #self.clothScene.refreshMotionConstraints()
        #self.clothScene.refreshCloth()
        #self.clothScene.clearInterpolation()

        self.handleNode.clearHandles()
        self.handleNode.addVertices(verts=[1552, 2090, 1525, 954, 1800, 663, 1381, 1527, 1858, 1077, 759, 533, 1429, 1131])
        self.handleNode.setOrgToCentroid()
        #print("org = " + str(self.handleNode.org))
        if self.interactiveHandleNode:
            self.handleNode.usingTargets = False
            self.viewer.interactors[2].frame.org = self.handleNode.org
        elif self.randomHandleTargetSpline:
            self.handleNode.usingTargets = True
            self.handleNode.clearTargetSpline()
            dt = self.handleTargetSplineWindow / self.numHandleTargetSplinePoints
            #debugging
            self.debuggingBoxes.clear()
            self.debuggingBoxes.append(pyutils.BoxFrame(c0=self.handleTargetSplineGlobalBounds[0], c1=self.handleTargetSplineGlobalBounds[1], org=self.handleNode.org))
            #end debugging
            #print("org = " + str(self.handleNode.org))
            for i in range(self.numHandleTargetSplinePoints):
                t = dt + dt*i
                pos = self.handleNode.targetSpline.pos(t=t)
                localDriftRange = np.array(self.handleTargetSplineLocalBounds)
                localDriftRange[0] = np.minimum(localDriftRange[0], self.handleTargetSplineGlobalBounds[0]+self.handleNode.org-pos)
                localDriftRange[1] = np.maximum(localDriftRange[1],
                                                self.handleTargetSplineGlobalBounds[1] + self.handleNode.org - pos)
                self.debuggingBoxes.append(pyutils.BoxFrame(c0=localDriftRange[0],
                                                            c1=localDriftRange[1],
                                                            org=pos))
                delta = np.array([random.uniform(localDriftRange[1][0], localDriftRange[0][0]),
                                  random.uniform(localDriftRange[1][1], localDriftRange[0][1]),
                                  random.uniform(localDriftRange[1][2], localDriftRange[0][2])])
                newpos = pos + delta
                self.handleNode.addTarget(t=t, pos=newpos)
        elif self.randomHandleTargetLinear:
            self.handleNode.usingTargets = True
            #draw initial pos
            oldOrg = np.array(self.handleNode.org)
            self.handleNode.org = self.handleTargetLinearInitialRange.sample(1)[0]
            disp = self.handleNode.org-oldOrg
            self.clothScene.translateCloth(0, disp)
            self.handleNode.clearTargetSpline()
            self.handleNode.addTarget(t=self.handleTargetLinearWindow, pos=self.handleTargetLinearEndRange.sample(1)[0])

        self.target = pyutils.getVertCentroid(verts=self.CP0Feature.verts, clothscene=self.clothScene)
        self.dart_world.skeletons[0].q = [0, 0, 0, self.target[0], self.target[1], self.target[2]]

        self.reset_number += 1

        #self.handleNode.reset()
        if self.handleNode is not None:
            #self.handleNode.setTransform(self.robot_skeleton.bodynodes[8].T)
            self.handleNode.recomputeOffsets()

        if self.gripper is not None:
            self.gripper.setTransform(self.robot_skeleton.bodynodes[8].T)

        return self._get_obs()

    def updateClothCollisionStructures(self, capsules=False, hapticSensors=False):
        #collision spheres creation
        a=0
        
        fingertip = np.array([0.0, -0.06, 0.0])
        z = np.array([0.,0,0])
        cs0 = self.robot_skeleton.bodynodes[1].to_world(z)
        cs1 = self.robot_skeleton.bodynodes[2].to_world(z)
        cs2 = self.robot_skeleton.bodynodes[16].to_world(z)
        cs3 = self.robot_skeleton.bodynodes[16].to_world(np.array([0,0.175,0]))
        cs4 = self.robot_skeleton.bodynodes[4].to_world(z)
        cs5 = self.robot_skeleton.bodynodes[6].to_world(z)
        cs6 = self.robot_skeleton.bodynodes[7].to_world(z)
        cs7 = self.robot_skeleton.bodynodes[8].to_world(z)
        cs8 = self.robot_skeleton.bodynodes[8].to_world(fingertip)
        cs9 = self.robot_skeleton.bodynodes[10].to_world(z)
        cs10 = self.robot_skeleton.bodynodes[12].to_world(z)
        cs11 = self.robot_skeleton.bodynodes[13].to_world(z)
        cs12 = self.robot_skeleton.bodynodes[14].to_world(z)
        cs13 = self.robot_skeleton.bodynodes[14].to_world(fingertip)
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


        collisionSpheresInfo = np.concatenate([cs0, csVars0, cs1, csVars1, cs2, csVars2, cs3, csVars3, cs4, csVars4, cs5, csVars5, cs6, csVars6, cs7, csVars7, cs8, csVars8, cs9, csVars9, cs10, csVars10, cs11, csVars11, cs12, csVars12, cs13, csVars13]).ravel()
        
        self.clothScene.setCollisionSpheresInfo(collisionSpheresInfo)
        
        if capsules is True:
            #collision capsules creation
            collisionCapsuleInfo = np.zeros((14,14))
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
            self.clothScene.setCollisionCapsuleInfo(collisionCapsuleInfo)
            
        if hapticSensors is True:
            hapticSensorLocations = np.concatenate([cs0, cs1, cs2, cs3, cs4, LERP(cs4, cs5, 0.33), LERP(cs4, cs5, 0.66), cs5, LERP(cs5, cs6, 0.33), LERP(cs5,cs6,0.66), cs6, cs7, cs8, cs9, LERP(cs9, cs10, 0.33), LERP(cs9, cs10, 0.66), cs10, LERP(cs10, cs11, 0.33), LERP(cs10, cs11, 0.66), cs11, cs12, cs13])
            self.clothScene.setHapticSensorLocations(hapticSensorLocations)

            
    def getViewer(self, sim, title=None, extraRenderFunc=None, inputFunc=None):
        return DartClothEnv.getViewer(self, sim, title, self.extraRenderFunction, self.inputFunc)
        
    def extraRenderFunction(self):
        #print("extra render function")
        
        GL.glBegin(GL.GL_LINES)
        GL.glVertex3d(0,0,0)
        GL.glVertex3d(-1,0,0)
        GL.glEnd()

        self.CP0Feature.drawProjectionPoly(fillColor=[0., 1.0, 0.0])

        armProgress = self.armSleeveProgress()
        self.clothScene.drawText(x=360, y=self.viewer.viewport[3] - 25,
                                 text="Arm progress = " + str(armProgress),
                                 color=(0., 0, 0))
        renderUtils.drawProgressBar(topLeft=[600, self.viewer.viewport[3] - 12], h=16, w=60,
                                    progress=armProgress / self.armLength, color=[0.0, 3.0, 0])

        #render debugging boxes
        if self.drawDebuggingBoxes:
            for ix,b in enumerate(self.debuggingBoxes):
                c = self.debuggingColors[ix]
                GL.glColor3d(c[0],c[1],c[2])
                b.draw()
                #for s in b.sample(50):
                #    self.viewer.drawSphere(p=s, r=0.01)

        #render the vertex handleNode(s)/Handle(s)
        if self.handleNode is not None:
            self.handleNode.draw()

        if self.gripper is not None:
            self.gripper.setTransform(self.robot_skeleton.bodynodes[8].T)
            self.gripper.draw()
            if self.clothScene is not None and False:
                vix = self.clothScene.getVerticesInShapeFrame(self.gripper)
                GL.glColor3d(0,0,1.)
                for v in vix:
                    p = self.clothScene.getVertexPos(vid=v)
                    GL.glPushMatrix()
                    GL.glTranslated(p[0], p[1], p[2])
                    GLUT.glutSolidSphere(0.005, 10, 10)
                    GL.glPopMatrix()
            
        m_viewport = GL.glGetIntegerv(GL.GL_VIEWPORT)
        
        textX = 15.
        if self.renderForceText:
            HSF = self.clothScene.getHapticSensorObs()
            for i in range(self.clothScene.getNumHapticSensors()):
                self.clothScene.drawText(x=textX, y=60.+15*i, text="||f[" + str(i) + "]|| = " + str(np.linalg.norm(HSF[3*i:3*i+3])), color=(0.,0,0))
            textX += 160
        
        #draw 2d HUD setup
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glOrtho(0, m_viewport[2], 0, m_viewport[3], -1, 1)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPushMatrix()
        GL.glLoadIdentity()
        GL.glDisable(GL.GL_CULL_FACE);
        #GL.glClear(GL.GL_DEPTH_BUFFER_BIT);
        
        #draw the load bars
        if self.renderDofs:
            #draw the load bar outlines
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_LINE)
            GL.glColor3d(0,0,0)
            GL.glBegin(GL.GL_QUADS)
            for i in range(len(self.robot_skeleton.q)):
                y = 58+18.*i
                x0 = 120+70
                x1 = 210+70
                GL.glVertex2d(x0, y)
                GL.glVertex2d(x0, y+15)
                GL.glVertex2d(x1, y+15)
                GL.glVertex2d(x1, y)
            GL.glEnd()
            #draw the load bar fills
            GL.glPolygonMode(GL.GL_FRONT_AND_BACK, GL.GL_FILL)
            for i in range(len(self.robot_skeleton.q)):
                qlim = self.limits(i)
                qfill = (self.robot_skeleton.q[i]-qlim[0])/(qlim[1]-qlim[0])
                y = 58+18.*i
                x0 = 121+70
                x1 = 209+70
                x = LERP(x0,x1,qfill)
                xz = LERP(x0,x1,(-qlim[0])/(qlim[1]-qlim[0]))
                GL.glColor3d(0,2,3)
                GL.glBegin(GL.GL_QUADS)
                GL.glVertex2d(x0, y+1)
                GL.glVertex2d(x0, y+14)
                GL.glVertex2d(x, y+14)
                GL.glVertex2d(x, y+1)
                GL.glEnd()
                GL.glColor3d(2,0,0)
                GL.glBegin(GL.GL_QUADS)
                GL.glVertex2d(xz-1, y+1)
                GL.glVertex2d(xz-1, y+14)
                GL.glVertex2d(xz+1, y+14)
                GL.glVertex2d(xz+1, y+1)
                GL.glEnd()
                GL.glColor3d(0,0,2)
                GL.glBegin(GL.GL_QUADS)
                GL.glVertex2d(x-1, y+1)
                GL.glVertex2d(x-1, y+14)
                GL.glVertex2d(x+1, y+14)
                GL.glVertex2d(x+1, y+1)
                GL.glEnd()
                GL.glColor3d(0,0,0)
                
                textPrefix = "||q[" + str(i) + "]|| = "
                if i < 10:
                    textPrefix = "||q[0" + str(i) + "]|| = "
                    
                self.clothScene.drawText(x=30, y=60.+18*i, text=textPrefix + '%.2f' % qlim[0], color=(0.,0,0))
                self.clothScene.drawText(x=x0, y=60.+18*i, text='%.3f' % self.robot_skeleton.q[i], color=(0.,0,0))
                self.clothScene.drawText(x=x1+2, y=60.+18*i, text='%.2f' % qlim[1], color=(0.,0,0))

        self.clothScene.drawText(x=15 , y=600., text='Friction: %.2f' % self.clothScene.getFriction(), color=(0., 0, 0))
        #f = self.clothScene.getHapticSensorObs()
        f = np.zeros(66)
        maxf_mag = 0

        for i in range(int(len(f)/3)):
            fi = f[i*3:i*3+3]
            #print(fi)
            mag = np.linalg.norm(fi)
            #print(mag)
            if mag > maxf_mag:
                maxf_mag = mag
        #exit()
        self.clothScene.drawText(x=15, y=620., text='Max force (1 dim): %.2f' % np.amax(f), color=(0., 0, 0))
        self.clothScene.drawText(x=15, y=640., text='Max force (3 dim): %.2f' % maxf_mag, color=(0., 0, 0))


        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glPopMatrix()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glPopMatrix()

    def inputFunc(self, repeat=False):
        pyutils.inputGenie(domain=self, repeat=repeat)

    def viewer_setup(self):
        if self._get_viewer().scene is not None:
            self._get_viewer().scene.tb.trans[2] = -3.5
            self._get_viewer().scene.tb._set_theta(180)
            self._get_viewer().scene.tb._set_phi(180)
            self.track_skeleton_id = 0


    def armSleeveProgress(self):
        # return the progress of the arm through the 1st sleeve seam
        limblines = []
        fingertip = np.array([0.0, -0.07, 0.0])
        end_effector = self.robot_skeleton.bodynodes[8].to_world(fingertip)
        if self.arm == 2:
            end_effector = self.robot_skeleton.bodynodes[14].to_world(fingertip)
        armProgress = 0

        if self.CP0Feature.plane is not None:
            armProgress = -np.linalg.norm(end_effector - self.CP0Feature.plane.org)

        if self.arm == 1:
            limblines.append([self.robot_skeleton.bodynodes[8].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[8].to_world(fingertip)])
            limblines.append([self.robot_skeleton.bodynodes[7].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[8].to_world(np.zeros(3))])
            limblines.append([self.robot_skeleton.bodynodes[6].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[7].to_world(np.zeros(3))])
            limblines.append([self.robot_skeleton.bodynodes[4].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[6].to_world(np.zeros(3))])
        elif self.arm == 2:
            limblines.append([self.robot_skeleton.bodynodes[14].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[14].to_world(fingertip)])
            limblines.append([self.robot_skeleton.bodynodes[13].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[14].to_world(np.zeros(3))])
            limblines.append([self.robot_skeleton.bodynodes[12].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[13].to_world(np.zeros(3))])
            limblines.append([self.robot_skeleton.bodynodes[10].to_world(np.zeros(3)),
                              self.robot_skeleton.bodynodes[12].to_world(np.zeros(3))])

        if self.armLength < 0:
            self.armLength = 0.
            for line in limblines:
                self.armLength += np.linalg.norm(line[1] - line[0])
        contains = False
        intersection_ix = -1
        intersection_depth = -1.0
        for ix, line in enumerate(limblines):
            line_contains, intersection_dist, intersection_point = self.CP0Feature.contains(l0=line[0], l1=line[1])
            if line_contains is True:
                intersection_ix = ix
                intersection_depth = intersection_dist
                contains = True

        if contains is True:
            armProgress = -intersection_depth
            for i in range(intersection_ix + 1):
                armProgress += np.linalg.norm(limblines[i][1] - limblines[i][0])

        return armProgress

def LERP(p0, p1, t):
    return p0 + (p1-p0)*t